"""
Orchestrates the full pipeline per (sentence, source) pair -- not per
sentence -- since a sentence citing multiple sources gets checked once per
citation, per the integration note.
"""
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional

from src.call_logger import log_llm_call
from src.citation_parser import parse_citation_markers
from src.content_fetcher import (ContentResult, fetch_content,
                                 fetch_source_content, is_usable_abstract)
from src.crossref import CrossrefResult, resolve_doi
from src.hybrid_judge_adapter import run_hybrid_luna_sol_cascade
from src.llm_wrapper import get_verdict as _get_verdict
from src.pmc_id_converter import PmcIdResult, convert_doi
from src.progress import ProgressTracker
from src.report_text_stage import (final_vote_quotes,
                                   maybe_run_report_text_stage,
                                   sol_not_established_votes,
                                   unsupported_sol_votes_from_judge_payload)

_CALL_LOG_KEYS = (
    "case_id", "call_key", "model", "vote_slot", "attempt_number", "validation_status", "failure_category",
    "result", "paid", "offline_mock", "prompt_tokens", "completion_tokens", "raw_reply",
)


def compact_call_log(call_log: object) -> list[dict]:
    """The judge call ledger without the repeated prompt and input text, so it stays small enough to store."""
    if not isinstance(call_log, list):
        return []
    return [{key: entry[key] for key in _CALL_LOG_KEYS if key in entry} for entry in call_log if isinstance(entry, dict)]

AUTHOR_YEAR_RE = re.compile(r"\(([^()]+?),\s*(\d{4})\)")
AUTHOR_YEAR_PAREN_RE = re.compile(
    r"([A-Z][A-Za-z'’.-]+(?:,\s*[A-Z]\.)*(?:\s+(?:et\s+al\.|and|&)\s*(?:[A-Z][A-Za-z'’.-]+)?)?)\s*\((\d{4})\)"
)
AUTHOR_YEAR_PLAIN_RE = re.compile(
    r"([A-Z][A-Za-z'’.-]+(?:,\s*[A-Z]\.)*(?:\s+(?:et\s+al\.|and|&)\s*(?:[A-Z][A-Za-z'’.-]+)?)?)\s*,\s*(\d{4})"
)

_VERDICT_LABELS = {
    "supported": "Supported",
    "unsupported": "Not supported",
    "uncertain": "Unclear",
}


def _is_internal_vote_summary(value: object) -> bool:
    if not isinstance(value, str):
        return False
    compact = value.strip().lower()
    if not compact:
        return False
    markers = (
        "luna cleared",
        "sol did not reach",
        "sol reached at least four valid",
        "vote slots",
        "sol was not invoked",
        "final decision is supported",
        "final decision is unsupported",
        "final decision is uncertain",
        "four-vote majority",
    )
    return any(marker in compact for marker in markers)


def get_verdict(
    claim: str,
    excerpt: str,
    source_type: str,
    citation_context: str,
    model: Optional[str] = None,
    sampling: Optional[dict] = None,
):
    """Compatibility wrapper for the historical pipeline API.

    Production safety: never substitute a simulated judge result when the live
    API key is absent. The only valid no-key behavior is a clear error so the
    caller can decide whether to enable credentials or keep the pipeline in a
    non-production testing mode.
    """
    from src.config import OPENAI_API_KEY
    from src.llm_wrapper import VerdictResult

    live_mode = os.getenv("REFCHECK_LIVE_JUDGE", "").strip().lower() in {"1", "true", "yes", "on"}
    key_is_missing = not OPENAI_API_KEY or OPENAI_API_KEY.strip().lower() in {"offline", "mock", "disabled"}
    offline_opt_in = os.getenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "").strip().lower() in {"1", "true", "yes", "on"}
    if key_is_missing:
        if live_mode:
            raise RuntimeError("Missing OPENAI_API_KEY for production judge execution. Production execution is blocked until credentials are supplied.")
        if not offline_opt_in and not live_mode:
            raise RuntimeError("OPENAI_API_KEY is not set and REFCHECK_ALLOW_OFFLINE_SIMULATION is not enabled. The judge cannot run without explicit opt-in or credentials.")
        from src.hybrid_judge_adapter import run_hybrid_luna_sol_cascade
        cascade = run_hybrid_luna_sol_cascade(
            claim=claim,
            evidence=[{"location": "body" if source_type == "full_text" else "abstract", "text": excerpt}],
            source_type=source_type,
            citation_context=citation_context,
        )
        verdict = cascade.get("final_verdict", "uncertain")
        judge_quotes = final_vote_quotes(cascade)
        return VerdictResult(
            status="verdict",
            source_type=source_type,
            citation_context=citation_context,
            assertions_checked="offline_hybrid_adapter_v2_2_2",
            verdict=verdict,
            primary_family=None,
            secondary_family="none",
            evidence_quote=judge_quotes[0] if judge_quotes else "",
            omitted_contradicting_evidence="",
            source_attribution="not_applicable",
            explanation=cascade.get("explanation", "Offline hybrid judge returned a verdict."),
            raw_response=json.dumps({**cascade, "simulation": True}, ensure_ascii=False),
            prompt_tokens=0,
            completion_tokens=0,
            cached_tokens=0,
        )

    from src.hybrid_judge_adapter import run_hybrid_luna_sol_cascade
    cascade = run_hybrid_luna_sol_cascade(
        claim=claim,
        evidence=[{"location": "body" if source_type == "full_text" else "abstract", "text": excerpt}],
        source_type=source_type,
        citation_context=citation_context,
    )
    verdict = cascade.get("final_verdict", "uncertain")
    judge_quotes = final_vote_quotes(cascade)
    return VerdictResult(
        status="verdict",
        source_type=source_type,
        citation_context=citation_context,
        assertions_checked="hybrid_luna_sol_v2_2_2",
        verdict=verdict,
        primary_family=None,
        secondary_family="none",
        evidence_quote=judge_quotes[0] if judge_quotes else "",
        omitted_contradicting_evidence="",
        source_attribution="not_applicable",
        explanation=cascade.get("explanation", "Hybrid judge returned a verdict."),
        raw_response=json.dumps(cascade, ensure_ascii=False),
        prompt_tokens=0,
        completion_tokens=0,
        cached_tokens=0,
    )


@dataclass
class _Reference:
    """Normalized identity for one reference. Either a resolvable DOI, or the
    title + first_author + publication_year search keys the extended cascade
    needs when the citation carries no identifier. ``raw`` is the original
    bibliography text (used to derive the search keys when nothing explicit
    was supplied)."""
    number: Optional[int] = None
    doi: Optional[str] = None
    title: Optional[str] = None
    first_author: Optional[str] = None
    publication_year: Optional[int] = None
    raw: Optional[str] = None


def _reference_from(entry) -> _Reference:
    """Normalize a reference entry -- a DOI string (legacy shape) or an entry
    dict with any of doi/title/first_author/publication_year/raw."""
    if isinstance(entry, _Reference):
        return entry
    if isinstance(entry, str):
        return _Reference(doi=entry.strip())
    return _Reference(
        number=entry.get("number"),
        doi=(entry.get("doi") or "").strip() or None,
        title=entry.get("title"),
        first_author=entry.get("first_author"),
        publication_year=entry.get("publication_year"),
        raw=entry.get("raw"),
    )


def _refine_reference(reference: _Reference) -> _Reference:
    """Fill title / first_author / publication_year from the raw bibliography
    string when an identifier-free entry carries no explicit search keys, so
    it still reaches the extended retrieval cascade keyed on title + first
    author + year (Eduardo's audit issue: identifier-free citations could not
    reach the cascade through the pipeline)."""
    if reference.doi or (reference.title and reference.publication_year):
        return reference
    if not reference.raw:
        return reference
    try:
        from src.metadata.extraction import parse_reference_entry
        parsed = parse_reference_entry(reference.number if reference.number is not None else 0, reference.raw)
    except Exception:  # noqa: BLE001 -- never fail the pipeline over a best-effort parse
        return reference
    return _Reference(
        number=reference.number,
        doi=reference.doi,
        title=reference.title or parsed.title,
        first_author=reference.first_author or (parsed.authors[0].family if parsed.authors else None),
        publication_year=reference.publication_year or parsed.year,
        raw=reference.raw,
    )


def _cache_key(reference: _Reference) -> str:
    """One chain per unique reference, not per (sentence, citation) pair.
    DOI-bearing references key on the DOI; identifier-free ones key on the
    title + first author + year triple (so two sentences citing the same
    DOI-less paper share one extended fetch)."""
    if reference.doi:
        return reference.doi
    if reference.title or reference.first_author or reference.publication_year:
        return "nd:{0!r}:{1!r}:{2!r}".format(reference.title, reference.first_author, reference.publication_year)
    return "ndraw:{0!r}".format(reference.raw)


@dataclass
class _SourceChainResult:
    """The outcome of Crossref -> PMC ID conversion -> content fetch for one
    reference. Later stages are None when an earlier stage stopped the chain.
    For an identifier-free reference there is no Crossref/NCBI stage at all:
    crossref_result is a synthetic status="no_identifier" placeholder and the
    content fetch IS the whole chain (extended retrieval only)."""
    crossref_result: CrossrefResult
    id_result: Optional[PmcIdResult] = None
    content_result: Optional[ContentResult] = None


class SourceCache:
    """
    Caches the full retrieval chain per unique reference for the lifetime of
    one process_sentences() run. When the same reference is cited by more
    than one sentence -- common in real manuscripts -- this means Crossref,
    NCBI, and the content fetch each get hit once per unique reference
    instead of once per (sentence, citation) pair.

    A reference may be identified by a DOI (Crossref -> PMC ID -> content
    fetch) or, when it carries no identifier at all, directly by its title +
    first author + year through the extended retrieval cascade. Cache keys
    are _cache_key(reference), so DOI-less references get one shared chain
    per (title, author, year) triple, exactly like DOIs do.

    Locking is per-key rather than a single global lock, so sentences citing
    different references never block each other; only the rare case of two
    threads hitting the *same* new reference at the same instant briefly
    coordinates, and only one of them does the actual network work.
    """

    def __init__(self):
        self._results: dict[str, _SourceChainResult] = {}
        self._doi_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._doi_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._doi_locks[key] = lock
            return lock

    def get(self, reference, contact_email: str) -> _SourceChainResult:
        reference = _refine_reference(_reference_from(reference))
        key = _cache_key(reference)
        cached = self._results.get(key)
        if cached is not None:
            return cached

        with self._lock_for(key):
            # Re-check: another thread may have finished fetching this
            # reference while we were waiting for the lock.
            cached = self._results.get(key)
            if cached is not None:
                return cached

            id_result = None
            content_result = None

            if reference.doi:
                crossref_result = resolve_doi(reference.doi, contact_email=contact_email)
                if crossref_result.status == "resolved":
                    # Feed the extended-retrieval fallback with the citation
                    # metadata Crossref provided (title / first author / year) so
                    # it can identity-check OA / repository / publisher sources
                    # even when PubMed has no record for this DOI.
                    first_author = None
                    year = None
                    if crossref_result.authors:
                        first_author = crossref_result.authors[0]
                    if crossref_result.published_date:
                        _y = crossref_result.published_date.split("-")[0]
                        if _y.isdigit():
                            year = int(_y)

                    id_result = convert_doi(reference.doi, contact_email=contact_email)
                    if id_result.status == "converted":
                        content_result = fetch_source_content(
                            doi=reference.doi,
                            pmid=id_result.pmid,
                            pmcid=id_result.pmcid,
                            title=crossref_result.title,
                            first_author=first_author,
                            publication_year=year,
                            contact_email=contact_email,
                        )
                    elif id_result.status == "not_indexed":
                        # Not in PubMed: extended retrieval can still reach the
                        # paper through Crossref / Europe PMC / Unpaywall / OA
                        # publisher and repository sources keyed on title + author.
                        content_result = fetch_source_content(
                            doi=reference.doi,
                            title=crossref_result.title,
                            first_author=first_author,
                            publication_year=year,
                            contact_email=contact_email,
                        )
            else:
                # No identifier at all: the extended cascade keyed on title +
                # first author + year is the ONLY retrieval path (audit
                # issue: pipeline accepted only DOIs).
                crossref_result = CrossrefResult(doi="", status="no_identifier")
                content_result = fetch_source_content(
                    doi=None,
                    title=reference.title,
                    first_author=reference.first_author,
                    publication_year=reference.publication_year,
                    contact_email=contact_email,
                )

            result = _SourceChainResult(crossref_result, id_result, content_result)
            self._results[key] = result
            return result


@dataclass
class PairResult:
    sentence_id: str
    sentence_text: str
    ref_number: Optional[int] = None
    doi: Optional[str] = None
    status: str = "verdict"
    source_type: Optional[str] = None
    citation_context: Optional[str] = None
    verdict: Optional[str] = None
    assertions_checked: Optional[str] = None
    primary_family: Optional[str] = None
    secondary_family: Optional[str] = None
    evidence_quote: Optional[str] = None
    evidence_quotes: Optional[list[str]] = None
    omitted_contradicting_evidence: Optional[str] = None
    source_attribution: Optional[str] = None
    explanation: Optional[str] = None
    observation: Optional[str] = None
    reason: Optional[str] = None
    quote_guard_triggered: bool = False
    llm_call_attempted: bool = False
    error_message: Optional[str] = None
    report_text_result: Optional[dict] = None
    show_content: bool = False
    show_verification: bool = False
    sol_not_established_votes: int = 0
    sol_valid_votes: int = 0
    evidence_blocks: Optional[list[dict]] = None
    judge_quotes: Optional[list[str]] = None
    judge_explanations: Optional[list[str]] = None
    judge_call_log: Optional[list[dict]] = None

    def __post_init__(self):
        reply = None
        if isinstance(self.report_text_result, dict):
            reply = self.report_text_result.get("reply")

        if self.evidence_quotes is None:
            if isinstance(self.evidence_quote, str) and self.evidence_quote.strip():
                self.evidence_quotes = [self.evidence_quote.strip()]
            else:
                self.evidence_quotes = []
        else:
            self.evidence_quotes = [str(item).strip() for item in self.evidence_quotes if isinstance(item, str) and str(item).strip()]

        report_evidence = None
        if isinstance(reply, dict):
            report_evidence = reply.get("evidence_passage") or reply.get("evidence_quote") or reply.get("source_excerpt")
            if isinstance(report_evidence, str) and report_evidence.strip():
                self.evidence_quotes = [report_evidence.strip()]
                if self.evidence_quote is None or not str(self.evidence_quote).strip():
                    self.evidence_quote = report_evidence.strip()

        if self.evidence_quote is None and self.evidence_quotes:
            self.evidence_quote = self.evidence_quotes[0]

        if isinstance(self.explanation, str):
            cleaned = self.explanation.strip()
            if not cleaned or _is_internal_vote_summary(cleaned):
                self.explanation = None

        report_observation = None
        report_reason = None
        if isinstance(reply, dict):
            report_observation = reply.get("observation") or reply.get("reason") or reply.get("explanation")
            if _is_internal_vote_summary(report_observation or ""):
                report_observation = None
            report_reason = report_observation

        if report_observation:
            self.observation = report_observation
            self.reason = report_reason or report_observation
            return

        if isinstance(reply, dict):
            detailed = reply.get("observation") or reply.get("explanation") or reply.get("reason")
            if _is_internal_vote_summary(detailed):
                detailed = None
            if detailed:
                if not self.observation or self.observation == self.explanation:
                    self.observation = detailed
                if not self.reason or self.reason == self.explanation:
                    self.reason = detailed

        if self.explanation:
            if not self.observation:
                self.observation = self.explanation
            if not self.reason:
                self.reason = self.explanation
            return

        if not self.observation and isinstance(reply, dict):
            detailed = reply.get("observation") or reply.get("explanation") or reply.get("reason")
            if detailed and not _is_internal_vote_summary(detailed):
                self.observation = detailed
                self.reason = detailed


def _extract_evidence_quotes(raw_response: object, fallback_quote: Optional[str]) -> list[str]:
    candidates: list[str] = []

    def _add_if_text(value: object) -> None:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                candidates.append(cleaned)

    if isinstance(raw_response, dict):
        for key in ("evidence_quotes", "evidence_quote"):
            value = raw_response.get(key)
            if isinstance(value, list):
                for item in value:
                    _add_if_text(item)
            else:
                _add_if_text(value)
    elif isinstance(raw_response, list):
        for item in raw_response:
            _add_if_text(item)
    elif isinstance(raw_response, str):
        _add_if_text(raw_response)

    if fallback_quote is not None:
        _add_if_text(fallback_quote)

    deduped: list[str] = []
    seen: set[str] = set()
    for quote in candidates:
        if quote not in seen:
            seen.add(quote)
            deduped.append(quote)
    return deduped


def _apply_report_text_gate(
    pair_results: list[PairResult],
    metadata_report: Optional[dict] = None,
    reference_entries: Optional[list[dict]] = None,
    provider=None,
) -> list[PairResult]:
    """Gate report-text generation by judge verdict and metadata visibility.

    The judge verdict stays authoritative. Report text is only created for
    unsupported claim-source pairs whose reference is allowed to show content.
    """
    if not pair_results:
        return pair_results

    show_by_reference: dict[int, bool] = {}
    if isinstance(metadata_report, dict):
        declared_dois: dict[int, str | None] = {}
        if isinstance(reference_entries, list):
            for ref in reference_entries:
                if not isinstance(ref, dict):
                    continue
                number = ref.get("number")
                if number is None:
                    number = ref.get("reference_id")
                try:
                    key = int(number)
                except (TypeError, ValueError):
                    continue
                doi = ref.get("doi")
                declared_dois[key] = doi if isinstance(doi, str) and doi.strip() else None

        try:
            from reference.metadata_status_mapping import build_metadata_plan

            plan = build_metadata_plan(
                metadata_report,
                declared_dois=declared_dois or None,
                official_records=metadata_report.get("official_records") if isinstance(metadata_report.get("official_records"), dict) else None,
            )
            for decision in plan.get("decisions", []):
                try:
                    reference_id = int(decision.reference_id)
                except (TypeError, ValueError):
                    continue
                show_by_reference[reference_id] = bool(getattr(decision, "show_content", True))
        except Exception:
            show_by_reference = {}

    for pair in pair_results:
        if not isinstance(pair, PairResult):
            continue

        if pair.status != "verdict" or pair.verdict != "unsupported":
            pair.show_content = False
            # Uncertain pairs get a verification card (no model text) under the same reference visibility rule.
            pair.show_verification = bool(pair.status == "verdict" and pair.verdict == "uncertain" and show_by_reference.get(pair.ref_number, True))
            continue

        allowed = show_by_reference.get(pair.ref_number, True)
        pair.show_content = bool(allowed)
        if pair.show_content and pair.report_text_result is None:
            if not pair.evidence_blocks:
                # Never validate against anything but the evidence the judge saw.
                pair.report_text_result = {
                    "status": "report_text_failed",
                    "report_complete": False,
                    "verdict_preserved": pair.verdict,
                    "reason": "frozen judge evidence is unavailable for this pair",
                    "reply": {},
                    "errors": ["frozen judge evidence is unavailable for this pair"],
                }
                continue
            pair.report_text_result = maybe_run_report_text_stage(
                claim=pair.sentence_text,
                marker=f"[{pair.ref_number}]" if pair.ref_number is not None else "[unknown]",
                evidence=pair.evidence_blocks,
                source_type=pair.source_type or "abstract",
                citation_context=pair.citation_context or "sole",
                final_verdict=pair.verdict,
                judge_quotes=list(pair.judge_quotes or []),
                judge_explanations=list(pair.judge_explanations or []),
                show_content=True,
                provider=provider,
                case_id=f"{pair.sentence_id}::ref{pair.ref_number}",
            )
        elif not pair.show_content:
            pair.report_text_result = pair.report_text_result or {
                "status": "skipped",
                "reply": {},
                "reason": "show_content is false for this reference",
            }

    return pair_results


def process_pair(
    sentence_id: str,
    sentence_text: str,
    ref_number: int,
    reference,  # DOI string (legacy) or an entry dict/ref identity
    citation_context: str,
    contact_email: str,
    progress: Optional[ProgressTracker] = None,
    source_cache: Optional[SourceCache] = None,
    show_content: bool = True,
) -> PairResult:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    api_key_missing = not api_key or api_key.lower() in {"offline", "mock", "disabled"}
    if api_key_missing:
        if progress is not None:
            progress.emit("system", ref_number, "Reference [1] — OPENAI_API_KEY is not set; falling back to local parsing logic.")
        return PairResult(
            sentence_id,
            sentence_text,
            ref_number,
            reference.doi if isinstance(reference, _Reference) else None,
            status="source_could_not_be_opened",
            source_type="abstract",
            citation_context=citation_context,
            llm_call_attempted=False,
            error_message="OPENAI_API_KEY is not set; local parsing fallback was used instead of running a live judge.",
        )

    reference = _refine_reference(_reference_from(reference))
    cache = source_cache if source_cache is not None else SourceCache()
    chain = cache.get(reference, contact_email)
    crossref_result = chain.crossref_result
    doi = reference.doi

    no_identifier = crossref_result.status == "no_identifier"

    if not no_identifier:
        if crossref_result.status == "not_found":
            if progress is not None:
                progress.emit("doi", ref_number, f"Reference [{ref_number}] — DOI not found")
            return PairResult(sentence_id, sentence_text, ref_number, doi, status="doi_not_found")

        if crossref_result.status == "error":
            if progress is not None:
                progress.emit("system", ref_number, f"Reference [{ref_number}] — Crossref lookup failed, retrying")
            return PairResult(
                sentence_id, sentence_text, ref_number, doi,
                status="technical_failure",
                error_message=f"Crossref lookup failed: {crossref_result.error_message}",
            )

    if no_identifier and not (reference.title or reference.first_author or reference.publication_year):
        # Present in the reference list but carries no identifier and no
        # search keys -- there is literally nothing to retrieve by.
        if progress is not None:
            progress.emit(
                "system", ref_number,
                f"Reference [{ref_number}] — no DOI and no title / author / year to search by",
            )
        return PairResult(
            sentence_id, sentence_text, ref_number, doi,
            status="unresolvable_reference",
            error_message="Reference present but carries no DOI and no title / first author / year to search by.",
        )

    if not no_identifier and progress is not None:
        progress.emit("ids", ref_number, f"Reference [{ref_number}] — resolving PubMed / PubMed Central ID")
    id_result = chain.id_result

    not_indexed = (id_result is not None and id_result.status == "not_indexed")
    if not_indexed and progress is not None:
        progress.emit("ids", ref_number, f"Reference [{ref_number}] — not indexed in PubMed; trying extended retrieval")

    if progress is not None and not not_indexed:
        progress.emit("content", ref_number, f"Reference [{ref_number}] — fetching full text")
    content_result = chain.content_result

    if content_result is None:
        # Chain stopped before a content fetch: an ID error, or a purely
        # technical failure -- surface it, never a misleading content status.
        if id_result is not None and id_result.status == "error":
            if progress is not None:
                progress.emit("system", ref_number, f"Reference [{ref_number}] — ID lookup failed, retrying")
            return PairResult(
                sentence_id, sentence_text, ref_number, doi,
                status="technical_failure",
                error_message=f"ID conversion failed: {id_result.error_message}",
            )
        return PairResult(
            sentence_id, sentence_text, ref_number, doi,
            status="technical_failure",
            error_message="No content chain available for this reference.",
        )

    if content_result.status == "error":
        if progress is not None:
            progress.emit("system", ref_number, f"Reference [{ref_number}] — content fetch failed, retrying")
        return PairResult(
            sentence_id, sentence_text, ref_number, doi,
            status="technical_failure",
            error_message=f"Content fetch failed: {content_result.error_message}",
        )

    if content_result.status == "abstained":
        if progress is not None:
            progress.emit("system", ref_number, f"Reference [{ref_number}] — ambiguous source, skipped")
        return PairResult(
            sentence_id, sentence_text, ref_number, doi,
            status="source_abstained",
            error_message=content_result.error_message or "Ambiguous source match; retrieval abstained.",
        )

    if content_result.status == "unavailable":
        if not_indexed:
            if progress is not None:
                progress.emit("ids", ref_number, f"Reference [{ref_number}] — not indexed in PubMed")
            return PairResult(sentence_id, sentence_text, ref_number, doi, status="source_not_indexed")
        if progress is not None:
            progress.emit("content", ref_number, f"Reference [{ref_number}] — no accessible full text")
        return PairResult(sentence_id, sentence_text, ref_number, doi, status="source_could_not_be_opened")

    if content_result.status == "abstract_only" and not is_usable_abstract(content_result.text):
        if not_indexed:
            if progress is not None:
                progress.emit("ids", ref_number, f"Reference [{ref_number}] — not indexed in PubMed")
            return PairResult(sentence_id, sentence_text, ref_number, doi, status="source_not_indexed")
        if progress is not None:
            progress.emit("content", ref_number, f"Reference [{ref_number}] — abstract too short, no usable text")
        return PairResult(
            sentence_id, sentence_text, ref_number, doi,
            status="source_could_not_be_opened",
            error_message="Abstract was too short or a placeholder; not sent to the model.",
        )

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    api_key_missing = not api_key or api_key.lower() in {"offline", "mock", "disabled"}
    if api_key_missing:
        if progress is not None:
            progress.emit("system", ref_number, "Reference [1] — OPENAI_API_KEY is not set; falling back to local parsing logic.")
        return PairResult(
            sentence_id,
            sentence_text,
            ref_number,
            doi,
            status="source_could_not_be_opened",
            source_type="full_text" if content_result.status == "full_text" else "abstract",
            citation_context=citation_context,
            llm_call_attempted=False,
            error_message="OPENAI_API_KEY is not set; local parsing fallback was used instead of running a live judge.",
        )

    source_type = "full_text" if content_result.status == "full_text" else "abstract"
    if progress is not None:
        progress.emit(
            "llm",
            ref_number,
            f"Reference [{ref_number}] — comparing against {'full text' if source_type == 'full_text' else 'abstract'}",
        )

    _llm_start = time.monotonic()
    llm_result = get_verdict(
        claim=sentence_text,
        excerpt=content_result.text,
        source_type=source_type,
        citation_context=citation_context,
    )
    _llm_duration = time.monotonic() - _llm_start
    judge_payload = {}
    if llm_result.raw_response:
        try:
            judge_payload = json.loads(llm_result.raw_response)
        except (TypeError, ValueError):
            judge_payload = {}

    log_llm_call(
        claim=sentence_text,
        excerpt=content_result.text,
        source_type=source_type,
        citation_context=citation_context,
        raw_response=llm_result.raw_response,
        status=llm_result.status,
        error_message=llm_result.error_message,
        duration_seconds=_llm_duration,
    )

    if llm_result.status == "system_error":
        message = llm_result.error_message or "LLM comparison failed."
        if progress is not None:
            progress.emit("system", ref_number, f"Reference [{ref_number}] — LLM comparison failed: {message}")
        return PairResult(
            sentence_id, sentence_text, ref_number, doi,
            status="technical_failure",
            source_type=source_type,
            citation_context=citation_context,
            verdict=None,
            assertions_checked=llm_result.assertions_checked,
            llm_call_attempted=True,
            error_message=f"LLM comparison failed: {message}",
        )

    if progress is not None:
        progress.emit("verdict", ref_number, f"Reference [{ref_number}] — {_VERDICT_LABELS.get(llm_result.verdict, 'Unclear')}")

    # The report-text call is made later by _apply_report_text_gate, once metadata decides show_content.
    evidence_blocks = [{"location": "body" if source_type == "full_text" else "abstract", "text": content_result.text}]
    judge_quotes, judge_explanations = unsupported_sol_votes_from_judge_payload(judge_payload)
    detailed_explanation = llm_result.explanation
    evidence_quotes = final_vote_quotes(judge_payload) or _extract_evidence_quotes(judge_payload, llm_result.evidence_quote)
    if not evidence_quotes:
        evidence_quotes = [llm_result.evidence_quote] if llm_result.evidence_quote else []
    selected_evidence_quote = evidence_quotes[0] if evidence_quotes else llm_result.evidence_quote
    not_established_votes, valid_sol_votes = sol_not_established_votes(judge_payload)

    pair = PairResult(
        sentence_id, sentence_text, ref_number, doi,
        status="verdict",
        source_type=source_type,
        citation_context=citation_context,
        verdict=llm_result.verdict,
        assertions_checked=llm_result.assertions_checked,
        primary_family=llm_result.primary_family,
        secondary_family=llm_result.secondary_family,
        evidence_quote=selected_evidence_quote,
        evidence_quotes=evidence_quotes,
        omitted_contradicting_evidence=llm_result.omitted_contradicting_evidence,
        source_attribution=llm_result.source_attribution,
        explanation=llm_result.explanation,
        observation=detailed_explanation,
        reason=detailed_explanation,
        quote_guard_triggered=llm_result.quote_guard_triggered,
        llm_call_attempted=True,
        show_content=bool(show_content and llm_result.verdict == "unsupported"),
        evidence_blocks=evidence_blocks,
        judge_quotes=judge_quotes,
        judge_explanations=judge_explanations,
        judge_call_log=compact_call_log(judge_payload.get("call_log")),
        sol_not_established_votes=not_established_votes,
        sol_valid_votes=valid_sol_votes,
    )
    if pair.verdict != "unsupported" or not pair.show_content:
        pair.report_text_result = pair.report_text_result or {
            "status": "skipped",
            "reply": {},
            "reason": "report-text is not eligible for this verdict",
        }
    return pair


def _leading_author_surname(author_text: str) -> str | None:
    cleaned = author_text.strip().strip("., ")
    if not cleaned:
        return None
    if " et al" in cleaned.lower():
        cleaned = cleaned.split(" et al", 1)[0].strip()
    if " and " in cleaned.lower():
        cleaned = cleaned.split(" and ", 1)[0].strip()
    cleaned = cleaned.strip("., ")
    match = re.search(r"([A-Z][A-Za-z'\-.]+)", cleaned)
    return match.group(1) if match else None


def _reference_identity(entry) -> tuple[str | None, int | None, int | None]:
    raw = (entry.get("raw") if isinstance(entry, dict) else "").strip()
    if not raw:
        number = entry.get("number") if isinstance(entry, dict) else None
        return None, int(number) if isinstance(number, int) else None, None

    stripped = re.sub(r"^\s*\[\d+\]\s*", "", raw)
    year_match = re.search(r"\b(19|20)\d{2}\b", stripped)
    year = int(year_match.group(0)) if year_match else None
    author_part = stripped[: year_match.start()].strip() if year_match else stripped
    author_part = author_part.strip("., ")
    lead_author = _leading_author_surname(author_part)
    number = entry.get("number") if isinstance(entry, dict) else None
    return lead_author, int(number) if isinstance(number, int) else None, year


def resolve_reference_numbers_for_sentence(text: str, reference_list: dict[int, dict | str]) -> list[int]:
    ref_numbers = parse_citation_markers(text)
    if ref_numbers:
        return ref_numbers

    found: list[int] = []
    seen: set[int] = set()

    def add_number(number: int) -> None:
        if number is None or number in seen:
            return
        seen.add(number)
        found.append(number)

    for ref_number, entry in reference_list.items():
        if not isinstance(ref_number, int):
            continue
        lead_author, _, year = _reference_identity(entry)
        if not lead_author or year is None:
            continue
        for regex in (AUTHOR_YEAR_RE, AUTHOR_YEAR_PAREN_RE, AUTHOR_YEAR_PLAIN_RE):
            for match in regex.finditer(text):
                if len(match.groups()) == 2:
                    author_text, year_text = match.groups()
                else:
                    author_text, year_text = match.groups()[0], match.groups()[1]
                try:
                    citation_year = int(year_text)
                except ValueError:
                    continue
                if citation_year != year:
                    continue
                cue_author = _leading_author_surname(author_text)
                if cue_author and cue_author.lower() == lead_author.lower():
                    add_number(ref_number)

    return found


def process_sentence(
    sentence_id: str,
    sentence_text: str,
    reference_list: dict[int, dict | str],
    contact_email: str,
    progress: Optional[ProgressTracker] = None,
    source_cache: Optional[SourceCache] = None,
) -> list[PairResult]:
    ref_numbers = parse_citation_markers(sentence_text)
    if not ref_numbers:
        ref_numbers = resolve_reference_numbers_for_sentence(sentence_text, reference_list)

    if not ref_numbers:
        return [PairResult(sentence_id, sentence_text, status="no_citations_found")]

    citation_context = "sole" if len(ref_numbers) == 1 else "one_of_several"
    results: list[PairResult] = []

    for ref_number in ref_numbers:
        entry = reference_list.get(ref_number)
        if entry is None:
            if progress is not None:
                progress.emit("system", ref_number, f"Reference [{ref_number}] — cited but missing from the reference list")
            results.append(PairResult(
                sentence_id, sentence_text, ref_number,
                status="reference_not_in_list",
                error_message=f"Reference [{ref_number}] was cited but not found in the reference list.",
            ))
            continue

        results.append(process_pair(sentence_id, sentence_text, ref_number, entry, citation_context, contact_email, progress, source_cache))

    return results


def process_sentences(
    sentences: list[dict],
    reference_list: dict[int, dict | str],
    contact_email: str,
    max_workers: int = 6,
    progress: Optional[ProgressTracker] = None,
) -> list[PairResult]:
    from concurrent.futures import ThreadPoolExecutor

    source_cache = SourceCache()

    def _run(sentence: dict) -> list[PairResult]:
        return process_sentence(sentence["id"], sentence["text"], reference_list, contact_email, progress, source_cache)

    results: list[list[PairResult]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_run, sentences))

    return [pair for group in results for pair in group]