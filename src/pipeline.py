"""
Orchestrates the full pipeline per (sentence, source) pair -- not per
sentence -- since a sentence citing multiple sources gets checked once per
citation, per the integration note.
"""
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from src.call_logger import log_llm_call
from src.citation_parser import parse_citation_markers
from src.content_fetcher import (ContentResult, fetch_content,
                                 fetch_source_content, is_usable_abstract)
from src.crossref import CrossrefResult, resolve_doi
from src.llm_wrapper import get_verdict
from src.pmc_id_converter import PmcIdResult, convert_doi
from src.progress import ProgressTracker

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
    omitted_contradicting_evidence: Optional[str] = None
    source_attribution: Optional[str] = None
    explanation: Optional[str] = None
    quote_guard_triggered: bool = False
    llm_call_attempted: bool = False
    error_message: Optional[str] = None


def process_pair(
    sentence_id: str,
    sentence_text: str,
    ref_number: int,
    reference,  # DOI string (legacy) or an entry dict/ref identity
    citation_context: str,
    contact_email: str,
    progress: Optional[ProgressTracker] = None,
    source_cache: Optional[SourceCache] = None,
) -> PairResult:
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

    source_type = "full_text" if content_result.status == "full_text" else "abstract"
    if progress is not None:
        progress.emit(
            "llm",
            ref_number,
            f"Reference [{ref_number}] — comparing against {'full text' if source_type == 'full_text' else 'abstract'}",
        )

    _llm_start = time.monotonic()
    llm_result = get_verdict(sentence_text, content_result.text, source_type, citation_context)
    _llm_duration = time.monotonic() - _llm_start

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
        if progress is not None:
            progress.emit("system", ref_number, f"Reference [{ref_number}] — comparison failed, retrying")
        return PairResult(
            sentence_id, sentence_text, ref_number, doi,
            status="technical_failure",
            source_type=source_type,
            citation_context=citation_context,
            error_message=f"LLM comparison failed: {llm_result.error_message}",
            llm_call_attempted=True,
        )

    if progress is not None:
        progress.emit("verdict", ref_number, f"Reference [{ref_number}] — {_VERDICT_LABELS.get(llm_result.verdict, 'Unclear')}")

    return PairResult(
        sentence_id, sentence_text, ref_number, doi,
        status="verdict",
        source_type=source_type,
        citation_context=citation_context,
        verdict=llm_result.verdict,
        assertions_checked=llm_result.assertions_checked,
        primary_family=llm_result.primary_family,
        secondary_family=llm_result.secondary_family,
        evidence_quote=llm_result.evidence_quote,
        omitted_contradicting_evidence=llm_result.omitted_contradicting_evidence,
        source_attribution=llm_result.source_attribution,
        explanation=llm_result.explanation,
        quote_guard_triggered=llm_result.quote_guard_triggered,
        llm_call_attempted=True,
    )


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