from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import sys
import traceback
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pandas as pd

from src.aggregator import generate_report
from src.analysis_runner import run_content_and_metadata
from src.config import require_contact_email
from src.metadata.document import MetadataDoc
from src.metadata.extraction import build_declared_references
from src.metadata.report import build_report
from src.storage import atomic_write_text as _atomic_write
from src.text_parser import parse_pasted_text

NUMBERED_CITATION_RE = re.compile(r"\[(\d+(?:\s*(?:,|-)\s*\d+)*)\]")
AUTHOR_YEAR_RE = re.compile(r"\(([^()]+?),\s*(\d{4})\)")
AUTHOR_YEAR_PAREN_RE = re.compile(
    r"([A-Z][A-Za-z'’.-]+(?:,\s*[A-Z]\.)*(?:\s+(?:et\s+al\.?|and|&)\s*(?:[A-Z][A-Za-z'’.-]+)?)?)\s*\((\d{4})\)"
)
AUTHOR_YEAR_PLAIN_RE = re.compile(
    r"([A-Z][A-Za-z'’.-]+(?:,\s*[A-Z]\.)*(?:\s+(?:et\s+al\.?|and|&)\s*(?:[A-Z][A-Za-z'’.-]+)?)?)\s*,\s*(\d{4})"
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


def _reference_identity(entry: dict[str, Any]) -> tuple[str | None, int | None, int | None]:
    raw = (entry.get("raw") or "").strip()
    if not raw:
        number = entry.get("number")
        return None, int(number) if isinstance(number, int) else None, None

    stripped = re.sub(r"^\s*\[\d+\]\s*", "", raw)
    year_match = re.search(r"\b(19|20)\d{2}\b", stripped)
    year = int(year_match.group(0)) if year_match else None
    author_part = stripped[: year_match.start()].strip() if year_match else stripped
    author_part = author_part.strip("., ")
    lead_author = _leading_author_surname(author_part)
    number = entry.get("number")
    return lead_author, int(number) if isinstance(number, int) else None, year


def _infer_citation_token(sentence_text: str, ref_number: Any | None) -> str:
    """Return the real citation token from the manuscript, not an internal numeric ID."""
    if not sentence_text:
        return f"[{ref_number}]" if ref_number is not None else "citation"

    if ref_number is not None:
        try:
            ref_int = int(ref_number)
        except (TypeError, ValueError):
            ref_int = None
        if ref_int is not None:
            for match in NUMBERED_CITATION_RE.finditer(sentence_text):
                inner = match.group(1)
                for part in re.split(r"\s*(?:,|-)\s*", inner.strip()):
                    if not part:
                        continue
                    try:
                        if int(part) == ref_int:
                            return f"[{part}]"
                    except ValueError:
                        continue

    for regex in (AUTHOR_YEAR_RE, AUTHOR_YEAR_PAREN_RE, AUTHOR_YEAR_PLAIN_RE):
        for match in regex.finditer(sentence_text):
            return match.group(0)

    for match in NUMBERED_CITATION_RE.finditer(sentence_text):
        return match.group(0)

    return f"[{ref_number}]" if ref_number is not None else "citation"


def _normalize_citation_key(value: Any) -> str:
    """Canonicalize equivalent citation spellings to the same identity."""
    text = str(value if value is not None else "").strip()
    while len(text) >= 2 and text[0] == "(" and text[-1] == ")":
        text = text[1:-1].strip()
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def _build_match_case_key(sentence_id: Any, citation: Any, ref_number: Any, occurrence_index: int = 0) -> str:
    """Stable identity for a citation match row.

    Repeated mentions of the same citation within the same manuscript sentence or
    claim must not be treated as new issue rows. The occurrence index is kept in
    the function signature for compatibility with older callers, but it does not
    participate in the final key because identical citations in the same claim
    should collapse to a single case.
    """
    sentence_key = str(sentence_id if sentence_id is not None else "unknown")
    citation_key = _normalize_citation_key(citation)
    ref_key = str(ref_number) if ref_number is not None else "none"
    return f"{sentence_key}|{citation_key}|{ref_key}"


def _match_citations_to_references(sentences: list[dict[str, Any]], references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    ref_by_number = {int(ref.get("number")): ref for ref in references if isinstance(ref.get("number"), int)}
    ref_by_number.update({int(ref.get("number")): ref for ref in references if isinstance(ref.get("number"), str) and ref.get("number").isdigit()})

    for sentence in sentences:
        text = sentence.get("text", "")

        for occurrence_index, match in enumerate(NUMBERED_CITATION_RE.finditer(text)):
            inner = match.group(1)
            for part_index, part in enumerate(re.split(r"\s*(?:,|-)\s*", inner.strip())):
                if not part:
                    continue
                try:
                    ref_number = int(part)
                except ValueError:
                    continue
                matched_number = ref_by_number.get(ref_number)
                citation_token = f"[{part}]"
                match_dict = {
                    "sentence_id": sentence.get("sentence_id", sentence.get("id", "")),
                    "citation": citation_token,
                    "matched_reference": ref_number if matched_number is not None else None,
                    "match_rule": "numbered_reference_number",
                    "status": "matched" if matched_number is not None else "unmatched",
                }
                match_dict["case_key"] = _build_match_case_key(match_dict["sentence_id"], citation_token, ref_number, occurrence_index + part_index)
                match_dict["id"] = match_dict["case_key"]
                matches.append(match_dict)

        for regex in (AUTHOR_YEAR_RE, AUTHOR_YEAR_PAREN_RE, AUTHOR_YEAR_PLAIN_RE):
            for occurrence_index, author_match in enumerate(regex.finditer(text)):
                if len(author_match.groups()) == 2:
                    author_text, year_text = author_match.groups()
                    citation_text = author_match.group(0)
                else:
                    author_text, year_text = author_match.groups()[0], author_match.groups()[1]
                    citation_text = author_match.group(0)

                try:
                    year = int(year_text)
                except ValueError:
                    continue

                cue_author = _leading_author_surname(author_text)
                candidates = []
                for ref in references:
                    lead_author, ref_number, ref_year = _reference_identity(ref)
                    if lead_author and ref_year == year and lead_author.lower() == (cue_author or "").lower():
                        candidates.append(ref_number)
                matched_ref = min(candidates) if candidates else None
                match_dict = {
                    "sentence_id": sentence.get("sentence_id", sentence.get("id", "")),
                    "citation": citation_text,
                    "matched_reference": matched_ref,
                    "match_rule": "author_year_lead_author_and_year",
                    "status": "matched" if matched_ref is not None else "unmatched",
                }
                match_dict["case_key"] = _build_match_case_key(match_dict["sentence_id"], citation_text, matched_ref, occurrence_index)
                match_dict["id"] = match_dict["case_key"]
                matches.append(match_dict)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in matches:
        sentence_id = str(match.get("sentence_id") or match.get("id") or "").strip().casefold()
        claim_text = str(match.get("claim") or match.get("sentence_text") or match.get("text") or "").strip().casefold()
        citation = str(match.get("citation") or match.get("marker") or "").strip().casefold()
        reference_id = str(match.get("matched_reference") or match.get("reference_id") or match.get("reference") or "").strip().casefold()
        key_parts = (sentence_id, claim_text, citation, reference_id)
        key = "|".join(part for part in key_parts if part)
        if not key:
            key = json.dumps({
                "sentence_id": match.get("sentence_id"),
                "citation": match.get("citation"),
                "matched_reference": match.get("matched_reference"),
                "match_rule": match.get("match_rule"),
            }, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(match)

    return deduped


def _dedupe_matches_by_identity(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated rows for the same sentence/claim/citation/reference."""
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()

    for match in matches:
        sentence_id = str(match.get("sentence_id") or match.get("id") or "").strip().casefold()
        claim_text = re.sub(r"\s+", " ", str(match.get("claim") or match.get("sentence_text") or match.get("text") or "").strip()).casefold()
        citation = _normalize_citation_key(match.get("citation") or match.get("marker") or "")
        reference_id = str(match.get("matched_reference") or match.get("reference_id") or match.get("reference") or "").strip().casefold()
        key = "|".join(part for part in (sentence_id, claim_text, citation, reference_id) if part)

        if not key:
            key = json.dumps({
                "sentence_id": match.get("sentence_id"),
                "citation": match.get("citation"),
                "matched_reference": match.get("matched_reference"),
                "match_rule": match.get("match_rule"),
            }, sort_keys=True)

        if key in seen:
            continue
        seen.add(key)
        deduped.append(match)

    return deduped


def _is_generic_match_rule(value: Any) -> bool:
    if value is None:
        return True
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in {
        "author_year_lead_author_and_year",
        "numbered_reference_number",
        "local_match",
        "citation_match",
        "reference_number_match",
    }


def _is_generic_default_reason(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    generic_phrases = {
        "The manuscript citation was matched to the corresponding reference entry.",
        "The citation was checked against the corresponding reference entry.",
        "The manuscript citation was matched to the corresponding reference entry",
        "The citation was checked against the corresponding reference entry",
    }
    return text in generic_phrases


def _is_internal_vote_summary(value: Any) -> bool:
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


def _canonical_content_error_key(value: Any) -> str | None:
    """Normalize any legacy/verbose error label to the frozen JSON error taxonomy."""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    normalized = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not normalized:
        return None

    technical_failure_tokens = {
        "source_could_not_be_opened",
        "source_abstained",
        "source_obtained",
        "technical_failure",
        "hybrid_live_judge",
        "cascade_returned_an_uncertain_verdict",
        "luna_sol_review",
        "luna_sol_review_",
        "luna_sol_review_2",
        "abstained",
        "uncertain",
    }
    if normalized in technical_failure_tokens:
        return None

    generic_fallback_tokens = {
        "unsupported",
        "unsupported_citation",
        "citation_issue",
        "issue",
        "unknown",
        "error",
        "unmatched",
        "none",
    }
    if normalized in generic_fallback_tokens:
        return "source_does_not_say"

    alias_map = {
        "source_says_opposite": "source_says_opposite",
        "the_source_says_the_opposite": "source_says_opposite",
        "source_says_the_opposite": "source_says_opposite",
        "study_did_not_do_that": "study_did_not_do_that",
        "the_study_did_not_do_that": "study_did_not_do_that",
        "data_does_not_match_the_study": "data_mismatch",
        "data_mismatch": "data_mismatch",
        "inflated_figure": "inflated_figure",
        "figure_taken_out_of_context": "figure_out_of_context",
        "figure_out_of_context": "figure_out_of_context",
        "measure_confused": "measure_confused",
        "one_measure_confused_with_another": "measure_confused",
        "source_does_not_say": "source_does_not_say",
        "the_source_does_not_say": "source_does_not_say",
        "overstated_certainty": "overstated_certainty",
        "wrong_reference_details": "wrong_reference_details",
        "incorrect_reference_details": "wrong_reference_details",
        "retracted_article": "retracted_article",
        "doi_leads_elsewhere": "doi_leads_elsewhere",
        "citation_without_reference": "citation_without_reference",
        "fabricated_reference": "wrong_reference_details",
        "reference_list_bibliography": "wrong_reference_details",
    }

    if normalized in alias_map:
        return alias_map[normalized]

    generic_phrases = {
        "the_manuscript_citation_was_matched_to_the_corresponding_reference_entry",
        "the_citation_was_checked_against_the_corresponding_reference_entry",
        "this_citation_was_reviewed_against_the_referenced_material",
    }
    if normalized in generic_phrases:
        return None

    return None


def _is_technical_failure_reason(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    normalized = text.lower()
    failure_markers = (
        "source_could_not_be_opened",
        "source_abstained",
        "could not be opened",
        "source could not be opened",
        "abstained",
        "hybrid live judge",
        "cascade returned an uncertain verdict",
        "luna→sol review",
        "luna-sol review",
        "technical failure",
        "source obtained",
        "source_obtained",
    )
    return any(marker in normalized for marker in failure_markers)


def _is_canonical_issue_label(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    normalized = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    canonical_labels = {
        "source_says_opposite",
        "the_source_says_the_opposite",
        "study_did_not_do_that",
        "the_study_did_not_do_that",
        "data_mismatch",
        "data_does_not_match_the_study",
        "inflated_figure",
        "figure_out_of_context",
        "figure_taken_out_of_context",
        "measure_confused",
        "one_measure_confused_with_another",
        "source_does_not_say",
        "the_source_does_not_say",
        "overstated_certainty",
        "wrong_reference_details",
        "incorrect_reference_details",
        "retracted_article",
        "doi_leads_elsewhere",
        "citation_without_reference",
    }
    return normalized in canonical_labels


def _humanize_issue_reason(value: Any) -> str:
    """Convert internal issue keys into the user-facing explanation text."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if _is_generic_match_rule(text) or _is_technical_failure_reason(text):
        return ""

    normalized = text.lower().replace("-", "_").replace(" ", "_")
    lookup = {
        "source_says_opposite": "The source says the opposite",
        "the_source_says_the_opposite": "The source says the opposite",
        "the_study_did_not_do_that": "The study did not do that",
        "study_did_not_do_that": "The study did not do that",
        "data_does_not_match_the_study": "Data does not match the study",
        "data_mismatch": "Data does not match the study",
        "inflated_figure": "Inflated figure",
        "figure_out_of_context": "Figure taken out of context",
        "measure_confused": "One measure confused with another",
        "overstated_certainty": "Overstated certainty",
        "source_does_not_say": "The source does not say that",
        "incorrect_reference_details": "Incorrect reference details",
        "wrong_reference_details": "Incorrect reference details",
        "fabricated_reference": "Fabricated reference",
        "reference_list_bibliography": "reference list/bibliography",
        "citation_without_reference": "No corresponding bibliography entry was found",
    }
    return lookup.get(normalized, text)


def _enrich_local_matches(matches: list[dict[str, Any]], sentences: list[dict[str, Any]], references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach the exact manuscript sentence and matched bibliography entry to each local match."""
    sentence_lookup: dict[str | None, dict[str, Any]] = {}
    for sentence in sentences:
        sentence_id = sentence.get("sentence_id") or sentence.get("id")
        if sentence_id is not None:
            sentence_lookup[sentence_id] = sentence

    ref_by_number: dict[int, dict[str, Any]] = {}
    for ref in references:
        number = ref.get("number")
        try:
            ref_key = int(number)
        except (TypeError, ValueError):
            continue
        ref_by_number[ref_key] = ref

    enriched: list[dict[str, Any]] = []
    for match in matches:
        item = dict(match)
        sentence_id = item.get("sentence_id")
        sentence = sentence_lookup.get(sentence_id) or {}
        sentence_text = item.get("sentence_text") or sentence.get("sentence_text") or sentence.get("text") or ""
        if sentence_text:
            item["sentence_text"] = sentence_text
            item["claim"] = item.get("claim") or sentence_text

        if not item.get("case_key"):
            ref_number = item.get("matched_reference")
            citation = item.get("citation") or _infer_citation_token(sentence_text, ref_number)
            item["case_key"] = _build_match_case_key(sentence_id, citation, ref_number, 0)
        if not item.get("id"):
            item["id"] = item.get("case_key")

        ref_number = item.get("matched_reference")
        try:
            ref_number_int = int(ref_number)
        except (TypeError, ValueError):
            ref_number_int = None

        ref_entry = ref_by_number.get(ref_number_int) if ref_number_int is not None else None
        ref_text = ""
        if ref_entry is not None:
            ref_text = (
                ref_entry.get("reference_text")
                or ref_entry.get("raw")
                or ref_entry.get("text")
                or ref_entry.get("title")
                or ""
            )
            item["matched_reference"] = ref_number_int
            if ref_text:
                item["reference_text"] = ref_text
            item["source_url"] = item.get("source_url") or (ref_entry.get("source_url") or ref_entry.get("url"))
            item["doi"] = item.get("doi") or ref_entry.get("doi")

        status_text = str(item.get("status") or "").lower()
        reason_candidates = [
            item.get("reason"),
            item.get("explanation"),
            item.get("observation"),
            item.get("error_type"),
            item.get("primary_family"),
            item.get("secondary_family"),
            item.get("match_rule"),
        ]

        selected_reason = ""
        selected_error_type = None
        for candidate in reason_candidates:
            if not candidate:
                continue
            if _is_technical_failure_reason(candidate):
                continue
            key = _canonical_content_error_key(candidate)
            if key is not None:
                selected_error_type = key
                humanized = _humanize_issue_reason(candidate)
                if humanized and humanized not in {"matched", "supported", "ok", "pass"}:
                    selected_reason = humanized
                break
            humanized = _humanize_issue_reason(candidate)
            if _is_generic_default_reason(humanized):
                continue
            if humanized and humanized not in {"matched", "supported", "ok", "pass"}:
                selected_reason = humanized
                break

        if selected_error_type is None:
            selected_error_type = _canonical_content_error_key(item.get("error_type"))
        if selected_error_type is None:
            selected_error_type = _canonical_content_error_key(item.get("match_rule"))
        if selected_error_type is None:
            selected_error_type = _canonical_content_error_key(status_text)

        if selected_error_type is not None:
            item["error_type"] = selected_error_type

        if not selected_reason and status_text in {"matched", "supported", "ok", "pass"}:
            selected_reason = "The manuscript citation was matched to the corresponding reference entry."
        elif not selected_reason and _is_technical_failure_reason(status_text):
            selected_reason = ""
        elif not selected_reason:
            selected_reason = ""

        explicit_reason = item.get("reason")
        explicit_observation = item.get("observation")
        explicit_explanation = item.get("explanation")

        if explicit_reason and (_is_generic_default_reason(explicit_reason) or _is_technical_failure_reason(explicit_reason) or _is_internal_vote_summary(explicit_reason)):
            explicit_reason = ""
        if explicit_observation and (_is_generic_default_reason(explicit_observation) or _is_technical_failure_reason(explicit_observation) or _is_internal_vote_summary(explicit_observation)):
            explicit_observation = ""
        if explicit_explanation and (_is_generic_default_reason(explicit_explanation) or _is_technical_failure_reason(explicit_explanation) or _is_internal_vote_summary(explicit_explanation)):
            explicit_explanation = ""

        if explicit_observation:
            item["observation"] = explicit_observation
        elif explicit_explanation:
            item["observation"] = explicit_explanation
        else:
            item["observation"] = selected_reason or explicit_reason or ""

        if explicit_reason and not _is_generic_default_reason(explicit_reason):
            item["reason"] = explicit_reason
        elif explicit_observation and not _is_generic_default_reason(explicit_observation):
            item["reason"] = explicit_observation
        elif explicit_explanation and not _is_generic_default_reason(explicit_explanation):
            item["reason"] = explicit_explanation
        else:
            item["reason"] = selected_reason or explicit_reason or ""

        if item.get("reason") == "" and item.get("observation"):
            item["reason"] = item["observation"]
        if item.get("observation") == "" and item.get("reason"):
            item["observation"] = item["reason"]

        enriched.append(item)

    return enriched


def _extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        xml_bytes = zf.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    texts: list[str] = []
    for node in root.iterfind(".//w:t", ns):
        if node.text:
            texts.append(node.text)
    return "\n".join(texts)


def _extract_pdf_text(path: Path) -> str:
    pdf_reader = None
    try:
        from pypdf import PdfReader
        pdf_reader = PdfReader
    except Exception:
        try:
            import fitz  # PyMuPDF fallback
            pdf_reader = None
            doc = fitz.open(str(path))
            parts: list[str] = []
            for page in doc:
                text = page.get_text("text") or ""
                if text:
                    parts.append(text)
            doc.close()
            return "\n".join(parts)
        except Exception:
            return ""

    reader = pdf_reader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            parts.append(text)
    return "\n".join(parts)


def _extract_manuscript_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _extract_docx_text(path)
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    raise ValueError(f"Unsupported manuscript type: {path.suffix!r}. Use a .docx or .pdf file.")


def _citation_numbers(text: str) -> list[int]:
    matches = NUMBERED_CITATION_RE.findall(text)
    numbers: list[int] = []
    for match in matches:
        for part in re.split(r"\s*(?:,|-)\s*", match.strip()):
            if part:
                try:
                    numbers.append(int(part))
                except ValueError:
                    pass
    return sorted(set(numbers))


def _author_year_citations(text: str) -> list[tuple[str, int]]:
    citations: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    for regex in (AUTHOR_YEAR_RE, AUTHOR_YEAR_PAREN_RE, AUTHOR_YEAR_PLAIN_RE):
        for match in regex.finditer(text):
            if len(match.groups()) == 2:
                left, year_text = match.groups()
                author_text = left.strip()
            else:
                author_text, year_text = match.groups()[0], match.groups()[1]
            try:
                pair = (author_text.strip(), int(year_text))
            except ValueError:
                continue
            if pair not in seen:
                seen.add(pair)
                citations.append(pair)

    for match in re.finditer(r"\((\d{4})\)", text):
        year = int(match.group(1))
        start = max(0, match.start() - 200)
        prefix = text[start:match.start()].strip()
        prefix = prefix.rsplit(".", 1)[-1].strip()
        if not prefix or not re.search(r"[A-Za-z]", prefix):
            continue
        if re.search(r"\b(?:et\s+al\.|and|&|[A-Z][A-Za-z'’.-]+(?:,\s*[A-Z]\.)*)\b", prefix):
            author_text = prefix.strip(" ,;:()[]")
            pair = (author_text, year)
            if pair not in seen:
                seen.add(pair)
                citations.append(pair)

    return citations


def _suggest_reference_entries(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for num in _citation_numbers(text):
        refs.append({"number": num, "raw": f"[{num}]", "doi": None})

    for idx, (author_text, year) in enumerate(_author_year_citations(text), start=1):
        refs.append({
            "number": idx,
            "raw": f"{author_text} ({year})",
            "doi": None,
            "author": author_text.strip(),
            "year": year,
        })
    return refs


def _build_html_report(report: dict[str, Any]) -> str:
    sentence_rows = "".join(
        f"<li><strong>{escape(item.get('sentence_id', item.get('id', '')))}</strong>: {escape(item.get('text', item.get('sentence_text', '')))}</li>"
        for item in report["sentences"]
    )
    ref_rows = "".join(
        f"<li><strong>{escape(str(item['number']))}</strong>: {escape(item.get('raw', ''))}</li>"
        for item in report["references"]
    )
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <title>RefCheck report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 2rem; }}
    .meta {{ background: #f7f7f7; padding: 1rem; border-radius: 8px; margin-bottom: 1rem; }}
    ul {{ line-height: 1.6; }}
  </style>
</head>
<body>
  <h1>RefCheck orchestrator report</h1>
  <div class=\"meta\">
    <p><strong>Source:</strong> {escape(report['source_file'])}</p>
    <p><strong>Status:</strong> {escape(report['status'])}</p>
    <p><strong>Generated:</strong> {escape(report['generated_at'])}</p>
  </div>
  <h2>Detected sentences</h2>
  <ul>{sentence_rows}</ul>
  <h2>Detected references</h2>
  <ul>{ref_rows}</ul>
</body>
</html>
"""


def _write_excel(dataset: list[dict[str, Any]], output_path: Path) -> None:
    df = pd.DataFrame(dataset)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(output_path, index=False)


def _print_llm_judge_results(report: dict[str, Any]) -> None:
    """Print the real judge verdicts for debugging when the library pipeline is used."""
    print("\n[LLM JUDGE RESULTS]")
    for sentence in report.get("sentences", []):
        sentence_id = sentence.get("sentence_id") or sentence.get("id") or "unknown"
        sentence_text = sentence.get("sentence_text") or sentence.get("text") or ""
        print(f"- sentence {sentence_id}: {sentence_text[:140]}")
        for pair in sentence.get("pairs", []):
            if pair.get("status") == "verdict":
                print(
                    f"  [{pair.get('ref_number')}] verdict={pair.get('verdict')} "
                    f"source={pair.get('source_type')} explanation={pair.get('explanation') or ''}"
                )
    print("[END LLM JUDGE RESULTS]\n")


def _content_case_from_pair(pair: dict[str, Any], reply: dict[str, Any], reference_entry: dict[str, Any] | None) -> dict[str, Any]:
    """Spec 7.1 content case from a validated report-text reply."""
    ref_number = pair.get("ref_number")
    entry = reference_entry or {}
    source_label = _source_label(entry, ref_number)
    blocks = pair.get("evidence_blocks") or []
    has_body = any(isinstance(block, dict) and block.get("location") == "body" for block in blocks)
    case = {
        "reference_id": ref_number,
        "marker": f"[{ref_number}]",
        "kind": "content",
        "source_label": source_label,
        "error_type": reply["error_type"],
        "claim": reply["claim_marked"],
        "evidence": reply["evidence_passage"],
        "passage_origin": "full_text" if has_body else "abstract",
        "observation": reply["observation"],
        "reference": entry.get("raw"),
        "doi": entry.get("doi"),
    }
    if reply.get("evidence_context"):
        case["evidence_context"] = reply["evidence_context"]
    return case


_FULL_TEXT_CHECK_TEXT = "Only the abstract of the cited source was available. A full-text check is recommended."
_POTENTIALLY_UNSUPPORTED_TEXT = "The reviewed evidence did not establish support for this claim. Check it carefully against the cited source."
_VERIFICATION_TEXT = {
    "full_text_check_recommended": _FULL_TEXT_CHECK_TEXT,
    "potentially_unsupported": _POTENTIALLY_UNSUPPORTED_TEXT,
}


def _has_body_evidence(pair: dict[str, Any]) -> bool:
    blocks = pair.get("evidence_blocks") or []
    if any(isinstance(block, dict) and block.get("location") == "body" for block in blocks):
        return True
    return not blocks and pair.get("source_type") == "full_text"


def _classify_uncertain_pair(pair: dict[str, Any]) -> str | None:
    """Abstract only -> neutral card; body reviewed and >=4 Sol votes 'not established' -> orange card; else no card."""
    if not _has_body_evidence(pair):
        return "full_text_check_recommended"
    if int(pair.get("sol_not_established_votes") or 0) >= 4:
        return "potentially_unsupported"
    return None


def _verification_case_from_pair(pair: dict[str, Any], card_type: str, reference_entry: dict[str, Any] | None) -> dict[str, Any]:
    """Card for an uncertain pair: fixed text only, no judge explanation and no evidence panel."""
    ref_number = pair.get("ref_number")
    entry = reference_entry or {}
    return {
        "reference_id": ref_number,
        "marker": f"[{ref_number}]",
        "kind": "verification",
        "source_label": _source_label(entry, ref_number),
        "error_type": card_type,
        "claim": pair.get("sentence_text") or "",
        "passage_origin": "full_text" if _has_body_evidence(pair) else "abstract",
        "observation": _VERIFICATION_TEXT[card_type],
        "reference": entry.get("raw"),
        "doi": entry.get("doi"),
    }


def _uncertain_record(pair: dict[str, Any], card_type: str | None, shown: bool, quotes: list[str]) -> dict[str, Any]:
    """Every uncertain pair, shown or not, for run_record.json."""
    return {
        "sentence_id": pair.get("sentence_id"),
        "reference_id": pair.get("ref_number"),
        "source_type": pair.get("source_type"),
        "body_evidence_reviewed": _has_body_evidence(pair),
        "sol_not_established_votes": int(pair.get("sol_not_established_votes") or 0),
        "sol_valid_votes": int(pair.get("sol_valid_votes") or 0),
        "card_type": card_type,
        "shown_in_report": shown,
        "explanation": pair.get("explanation"),
        "evidence_quotes": quotes,
    }


def _collect_call_log(sentence_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every judge vote and report-text attempt of the run, tagged with the pair it belongs to."""
    rows: list[dict[str, Any]] = []
    for sentence in sentence_entries:
        for pair in sentence.get("pairs", []) or []:
            tag = {"sentence_id": pair.get("sentence_id"), "ref_number": pair.get("ref_number")}
            for entry in pair.get("judge_call_log") or []:
                rows.append({**tag, "stage": "judge", **entry})
            result = pair.get("report_text_result")
            for entry in (result.get("audit_ledger") or []) if isinstance(result, dict) else []:
                rows.append({**tag, **entry})
    return rows


def _normalize_library_report(report: dict[str, Any], reference_list: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Convert the LLM report into the flat match schema the browser UI expects."""
    normalized = dict(report)
    sentence_entries = normalized.get("sentences", [])
    matches: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    verification_cases: list[dict[str, Any]] = []
    uncertain_pairs: list[dict[str, Any]] = []
    seen: set[tuple[str | None, int | str | None]] = set()
    entry_by_number: dict[int, dict[str, Any]] = {}
    for entry in reference_list or []:
        try:
            entry_by_number[int(entry.get("number"))] = entry
        except (TypeError, ValueError, AttributeError):
            continue

    for sentence in sentence_entries:
        sentence_id = sentence.get("sentence_id") or sentence.get("id")
        sentence_text = sentence.get("sentence_text") or sentence.get("text") or ""
        for pair_index, pair in enumerate(sentence.get("pairs", [])):
            ref_number = pair.get("ref_number")
            if ref_number is None:
                continue
            key = (sentence_id, ref_number, pair_index)
            if key in seen:
                continue
            seen.add(key)

            raw_pair_status = str(pair.get("status") or "").strip().rstrip(".")
            pair_status = raw_pair_status or "issue"
            verdict = str(pair.get("verdict") or "").lower()
            if pair_status == "verdict":
                status = {"supported": "matched", "uncertain": "uncertain"}.get(verdict, "issue")
            elif pair_status:
                status = pair_status
            else:
                status = "issue"

            raw_match_rule = (
                pair.get("primary_family")
                or pair.get("secondary_family")
                or pair.get("match_rule")
                or pair_status
                or pair.get("verdict")
                or "llm_verdict"
            )
            match_rule = str(raw_match_rule).strip().rstrip(".") if raw_match_rule is not None else "llm_verdict"

            report_text_result = pair.get("report_text_result") if isinstance(pair.get("report_text_result"), dict) else None
            report_text_status = report_text_result.get("status") if report_text_result else None
            # Only a validated reply may reach the report.
            report_reply = report_text_result.get("reply") if report_text_status == "ready" else None
            report_error_type = report_reply.get("error_type") if isinstance(report_reply, dict) else None
            if isinstance(report_error_type, str) and report_error_type.strip():
                error_type = report_error_type.strip()
            elif pair_status == "verdict" and verdict != "unsupported":
                error_type = None
            else:
                error_type = _canonical_content_error_key(
                    pair.get("error_type")
                    or pair.get("primary_family")
                    or pair.get("secondary_family")
                    or pair.get("match_rule")
                    or pair.get("reason")
                    or pair.get("explanation")
                )

            evidence_quotes = pair.get("evidence_quotes")
            if isinstance(evidence_quotes, list):
                normalized_quotes = [str(item).strip() for item in evidence_quotes if isinstance(item, str) and str(item).strip()]
            elif isinstance(evidence_quotes, str):
                normalized_quotes = [evidence_quotes.strip()] if evidence_quotes.strip() else []
            else:
                normalized_quotes = []
            if not normalized_quotes:
                single_evidence = pair.get("evidence_quote") or pair.get("source_excerpt") or pair.get("excerpt")
                if isinstance(single_evidence, str) and single_evidence.strip():
                    normalized_quotes = [single_evidence.strip()]

            report_evidence_passage = report_reply.get("evidence_passage") if isinstance(report_reply, dict) else None
            if isinstance(report_evidence_passage, str) and report_evidence_passage.strip():
                normalized_quotes = [report_evidence_passage.strip()]

            source_excerpt = normalized_quotes[0] if normalized_quotes else ""
            reference_text = pair.get("reference_text") or pair.get("reference") or pair.get("reference_entry")

            report_observation = report_reply.get("observation") if isinstance(report_reply, dict) else None
            if isinstance(report_observation, str) and report_observation.strip() and not _is_internal_vote_summary(report_observation):
                sentence_observation = report_observation.strip()
            else:
                sentence_observation = pair.get("explanation") or pair.get("observation") or pair.get("reason") or pair.get("report_text")
                if _is_internal_vote_summary(sentence_observation):
                    sentence_observation = ""
            detailed_reason = sentence_observation or _humanize_issue_reason(error_type)
            citation_token = _infer_citation_token(sentence_text, ref_number)
            case_key = _build_match_case_key(sentence_id, citation_token, ref_number, pair_index)
            is_uncertain = pair_status == "verdict" and verdict == "uncertain"
            card_type = _classify_uncertain_pair(pair) if is_uncertain else None

            matches.append({
                "sentence_id": sentence_id,
                "sentence_text": sentence_text,
                "claim": sentence_text,
                "citation": citation_token,
                "status": status,
                "matched_reference": ref_number,
                "match_rule": match_rule,
                "error_type": error_type,
                "kind": "content",
                "verdict": verdict,
                "primary_family": pair.get("primary_family") or error_type,
                "secondary_family": pair.get("secondary_family") or "none",
                "source_type": pair.get("source_type"),
                "explanation": pair.get("explanation") or sentence_observation,
                "observation": sentence_observation,
                "reason": detailed_reason,
                "source_excerpt": source_excerpt,
                "evidence_quote": source_excerpt,
                "evidence_quotes": normalized_quotes,
                "reference_text": reference_text,
                "assertions_checked": pair.get("assertions_checked"),
                "case_key": case_key,
                "id": case_key,
                "report_text_status": report_text_status,
                "claim_marked": report_reply.get("claim_marked") if isinstance(report_reply, dict) else None,
                "evidence_passage": report_reply.get("evidence_passage") if isinstance(report_reply, dict) else None,
                "evidence_context": report_reply.get("evidence_context") if isinstance(report_reply, dict) else None,
                "verification_type": card_type,
            })

            if (
                pair_status == "verdict"
                and verdict == "unsupported"
                and pair.get("show_content")
                and isinstance(report_reply, dict)
            ):
                try:
                    cases.append(_content_case_from_pair(pair, report_reply, entry_by_number.get(int(ref_number))))
                except (KeyError, TypeError, ValueError):
                    pass
            elif is_uncertain:
                shown = bool(card_type and pair.get("show_verification"))
                uncertain_pairs.append(_uncertain_record(pair, card_type, shown, normalized_quotes))
                if shown:
                    try:
                        verification_cases.append(_verification_case_from_pair(pair, card_type, entry_by_number.get(int(ref_number))))
                    except (TypeError, ValueError):
                        pass

    normalized["citation_matches"] = matches
    normalized["cases"] = cases
    normalized["verification_cases"] = verification_cases
    normalized["uncertain_pairs"] = uncertain_pairs
    call_log = _collect_call_log(sentence_entries)
    normalized["call_log"] = call_log
    normalized["provider_attempts"] = sum(1 for entry in call_log if entry.get("paid"))
    normalized["summary"] = dict(normalized.get("summary", {}))
    normalized["summary"]["library_pipeline_used"] = True
    normalized["citation_count"] = len(matches)
    return normalized


def _ensure_reference_text_for_matches(matches: list[dict[str, Any]], reference_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backfill the bibliography/reference text from the matched reference number when downstream stages omit it."""
    if not matches:
        return matches

    ref_by_number: dict[int, dict[str, Any]] = {}
    for ref in reference_list:
        number = ref.get("number")
        try:
            ref_key = int(number)
        except (TypeError, ValueError):
            continue
        ref_by_number[ref_key] = ref

    for match in matches:
        if not isinstance(match, dict):
            continue
        if match.get("reference_text"):
            continue
        ref_number = match.get("matched_reference")
        try:
            ref_key = int(ref_number)
        except (TypeError, ValueError):
            continue
        ref_entry = ref_by_number.get(ref_key)
        if ref_entry is None:
            continue
        ref_text = (
            ref_entry.get("reference_text")
            or ref_entry.get("raw")
            or ref_entry.get("text")
            or ref_entry.get("title")
            or ""
        )
        if ref_text:
            match["reference_text"] = ref_text

    return matches


def _build_metadata_report(sentences: list[dict[str, Any]], reference_list: list[dict[str, Any]], body_text: str) -> dict[str, Any]:
    """Use the metadata module to build a realistic bibliography-status report per reference."""
    if _api_key_missing():
        return {
            "bibliography_status": [],
            "document_level_findings": [],
            "not_evaluated_checks": [],
            "official_records": {},
        }
    if not reference_list:
        return {
            "bibliography_status": [],
            "document_level_findings": [],
            "not_evaluated_checks": [],
            "official_records": {},
        }

    try:
        contact_email = None
        try:
            contact_email = require_contact_email()
        except RuntimeError:
            contact_email = None

        doc = MetadataDoc(sentences=sentences, body_text=body_text)
        declared_references = build_declared_references(reference_list)
        report = build_report(doc, declared_references, contact_email=contact_email)
        return report
    except Exception as exc:  # pragma: no cover - metadata is additive; local report should still work
        return {
            "bibliography_status": [],
            "document_level_findings": [],
            "not_evaluated_checks": [],
            "official_records": {},
            "error": str(exc),
        }


def _api_key_missing() -> bool:
    api_key = (os.getenv("OPENAI_API_KEY", "") or "").strip()
    return not api_key or api_key.lower() in {"offline", "mock", "disabled"}


def _default_report_catalog() -> dict[str, Any]:
    """Prefer the approved HTML catalog shipped with the bundle, else fall back to a minimal catalog."""
    root_dir = Path(__file__).resolve().parents[1]
    template_path = root_dir / "RefCheck_Report_Ultima_Plantilla.html"
    if template_path.exists():
        try:
            text = template_path.read_text(encoding="utf-8")
            match = re.search(r'<script id="report-error-types" type="application/json">(.*?)</script>', text, re.S)
            if match:
                payload = json.loads(match.group(1))
                if isinstance(payload, dict) and payload:
                    return payload
        except Exception:
            pass
    fallback = {
        "source_says_opposite": {"kind": "content", "label": {"en": "The source says the opposite"}},
        "study_did_not_do_that": {"kind": "content", "label": {"en": "The study did not do that"}},
        "data_mismatch": {"kind": "content", "label": {"en": "Data does not match the study"}},
        "inflated_figure": {"kind": "content", "label": {"en": "Inflated figure"}},
        "figure_out_of_context": {"kind": "content", "label": {"en": "Figure taken out of context"}},
        "measure_confused": {"kind": "content", "label": {"en": "One measure confused with another"}},
        "source_does_not_say": {"kind": "content", "label": {"en": "The source does not say that"}},
        "overstated_certainty": {"kind": "content", "label": {"en": "Overstated certainty"}},
        "wrong_reference_details": {"kind": "metadata", "label": {"en": "Incorrect reference details"}},
        "retracted_article": {"kind": "metadata", "label": {"en": "Retracted article"}},
        "doi_leads_elsewhere": {"kind": "metadata", "label": {"en": "DOI leads elsewhere"}},
        "citation_without_reference": {"kind": "metadata", "label": {"en": "No corresponding bibliography entry was found"}},
    }
    return fallback


def _load_validator_module():
    root_dir = Path(__file__).resolve().parents[1]
    validator_path = root_dir / "reference" / "validate_render_data.py"
    if not validator_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("ref_validate_render_data", validator_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply_publication_gate(report: dict[str, Any] | None, manifest: dict[str, Any] | None, official_records: dict[str, Any] | None, expected_manifest_sha256: str | None, *, catalog: dict[str, Any] | None = None, extra_errors: list[str] | None = None) -> dict[str, Any]:
    """Fail-closed publication gate for FINAL output.

    Returns a report-like dict with report_complete set according to the validator
    contract and a publication_status of either 'final' or 'draft'. extra_errors are
    upstream causes (failed report text, unknown status) that force a DRAFT.
    """
    report_data = copy.deepcopy(report) if isinstance(report, dict) else {}
    resolved_catalog = catalog if isinstance(catalog, dict) else _default_report_catalog()
    resolved_manifest = manifest if isinstance(manifest, dict) else {}
    resolved_official_records = official_records if isinstance(official_records, dict) else {}
    manifest_hash = str(expected_manifest_sha256 or "")

    module = _load_validator_module()
    validator = getattr(module, "validate_for_publication", None) if module is not None else None
    errors: list[str] = []
    if validator is not None:
        try:
            # The validator requires report_complete=true; upstream causes are applied below.
            candidate = {**report_data, "report_complete": True}
            errors = list(validator(candidate, resolved_catalog, resolved_manifest, resolved_official_records, manifest_hash))
        except Exception as exc:  # pragma: no cover - fail closed on import or runtime issues
            errors = [f"publication validation failed: {exc}"]
    else:
        errors = ["publication validation unavailable: validate_for_publication could not be imported"]
    errors = list(extra_errors or []) + errors

    if errors:
        report_data["report_complete"] = False
        report_data["publication_status"] = "draft"
        report_data["publication_errors"] = errors
        report_data["publication_reason"] = errors[0]
        return report_data

    report_data["report_complete"] = True
    report_data["publication_status"] = "final"
    report_data["publication_errors"] = []
    report_data["publication_reason"] = "validated"
    return report_data


def _judge_call_summary(call_log: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Compute the model names and counts from a judge call ledger when one exists."""
    if not isinstance(call_log, list):
        return {"models_called": [], "luna_calls": 0, "sol_calls": 0}

    seen: set[str] = set()
    models_called: list[str] = []
    luna_calls = 0
    sol_calls = 0
    for entry in call_log:
        if not isinstance(entry, dict):
            continue
        model_name = str(entry.get("model") or entry.get("model_identity") or "").strip().lower()
        if not model_name:
            arm = str(entry.get("arm") or "").strip().lower()
            if arm in {"luna", "gpt-5.6-luna"}:
                model_name = "gpt-5.6-luna"
            elif arm in {"sol", "gpt-5.6-sol"}:
                model_name = "gpt-5.6-sol"
        if model_name and model_name not in seen:
            seen.add(model_name)
            models_called.append(model_name)
        if model_name == "gpt-5.6-luna":
            luna_calls += 1
        elif model_name == "gpt-5.6-sol":
            sol_calls += 1
    return {"models_called": models_called, "luna_calls": luna_calls, "sol_calls": sol_calls}


def _hybrid_prompt_identity() -> dict[str, str]:
    """Return the configured hybrid prompt version and stable content hash for reporting."""
    try:
        from src.hybrid_judge_adapter import (_selected_hybrid_version,
                                              load_hybrid_prompt)
    except Exception:
        return {"judge_prompt_version": "unknown", "judge_prompt_hash": "unknown"}

    version = _selected_hybrid_version()
    prompt_text = load_hybrid_prompt("sole", version=version)
    digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    return {"judge_prompt_version": version, "judge_prompt_hash": digest}


def _has_real_judge_execution(library_report: dict[str, Any] | None) -> bool:
    """Detect judge execution from the real library payload even when it omits the top-level flag."""
    if not isinstance(library_report, dict):
        return False

    if library_report.get("judge_executed") is True or library_report.get("judge_ran") is True:
        return True

    call_log = library_report.get("call_log") or library_report.get("judge_call_log")
    if isinstance(call_log, list) and call_log:
        return True

    if isinstance(library_report.get("final_verdict"), str) and library_report.get("final_verdict").strip():
        return True

    for sentence in library_report.get("sentences", []) or []:
        if not isinstance(sentence, dict):
            continue
        for pair in sentence.get("pairs", []) or []:
            if not isinstance(pair, dict):
                continue
            if pair.get("llm_call_attempted") is True:
                return True
            verdict = str(pair.get("verdict") or "").strip().lower()
            if verdict in {"supported", "unsupported", "uncertain", "matched", "mismatch"}:
                return True
    return False


def _judge_execution_state(*, api_key_missing: bool, simulation_mode: bool, library_report: dict[str, Any] | None) -> dict[str, Any]:
    """Describe what happened to judge execution without conflating it with file parsing success."""
    prompt_identity = _hybrid_prompt_identity()

    if simulation_mode:
        return {
            "judge_executed": False,
            "judge_status": "simulated",
            "judge_blocker": "explicit_opt_in",
            "judge_prompt_version": prompt_identity["judge_prompt_version"],
            "judge_prompt_hash": prompt_identity["judge_prompt_hash"],
            "provider_attempts": 0,
            "final_verdict_status": "not_applicable",
            "final_verdict": None,
            "scientific_verdict": False,
            "verdict_provenance": "simulation",
            "models_called": [],
            "luna_calls": 0,
            "sol_calls": 0,
        }

    if library_report is not None and _has_real_judge_execution(library_report):
        call_summary = _judge_call_summary(library_report.get("call_log") or library_report.get("judge_call_log"))
        prompt_version = library_report.get("prompt_version") or library_report.get("judge_prompt_version") or prompt_identity["judge_prompt_version"]
        prompt_hash = library_report.get("prompt_hash") or library_report.get("judge_prompt_hash") or prompt_identity["judge_prompt_hash"]
        provider_attempts = int(library_report["provider_attempts"]) if "provider_attempts" in library_report else len(library_report.get("call_log") or library_report.get("judge_call_log") or [])
        return {
            "judge_executed": True,
            "judge_status": "executed",
            "judge_blocker": None,
            "judge_prompt_version": prompt_version,
            "judge_prompt_hash": prompt_hash,
            "provider_attempts": provider_attempts,
            "final_verdict_status": "complete",
            "final_verdict": library_report.get("final_verdict"),
            "scientific_verdict": bool(library_report.get("scientific_verdict", False)),
            "verdict_provenance": library_report.get("verdict_provenance") or "judge",
            "models_called": call_summary["models_called"],
            "luna_calls": call_summary["luna_calls"],
            "sol_calls": call_summary["sol_calls"],
        }

    if api_key_missing:
        return {
            "judge_executed": False,
            "judge_status": "not_executed",
            "judge_blocker": "missing_credentials",
            "judge_prompt_version": prompt_identity["judge_prompt_version"],
            "judge_prompt_hash": prompt_identity["judge_prompt_hash"],
            "provider_attempts": 0,
            "final_verdict_status": "not_applicable",
            "final_verdict": None,
            "scientific_verdict": False,
            "verdict_provenance": "local_parse_only",
            "models_called": [],
            "luna_calls": 0,
            "sol_calls": 0,
        }

    return {
        "judge_executed": False,
        "judge_status": "failed",
        "judge_blocker": "unknown",
        "judge_prompt_version": prompt_identity["judge_prompt_version"],
        "judge_prompt_hash": prompt_identity["judge_prompt_hash"],
        "provider_attempts": 0,
        "final_verdict_status": "not_applicable",
        "final_verdict": None,
        "scientific_verdict": False,
        "verdict_provenance": "local_parse_only",
        "models_called": [],
        "luna_calls": 0,
        "sol_calls": 0,
    }


def _build_library_report(sentences: list[dict[str, Any]], reference_list: list[dict[str, Any]], body_text: str) -> dict[str, Any] | None:
    """Run the real library pipeline. Returns None only when OPENAI_API_KEY is absent; every other failure raises."""
    if _api_key_missing():
        print("[DEBUG] Library pipeline skipped: OPENAI_API_KEY is not set.")
        return None

    contact_email = require_contact_email()  # raises RuntimeError when CONTACT_EMAIL is missing

    try:
        reference_map: dict[int, dict[str, Any] | str] = {}
        for entry in reference_list:
            number = entry.get("number")
            if number is None:
                continue
            try:
                reference_map[int(number)] = entry
            except (TypeError, ValueError):
                continue

        reference_entries = [dict(entry) for entry in reference_list]
        pair_results, metadata_report = run_content_and_metadata(
            sentences,
            reference_map,
            reference_entries,
            body_text=body_text,
            contact_email=contact_email,
        )
        report = generate_report(pair_results)
        report["bibliography_status"] = metadata_report.get("bibliography_status", [])
        report["document_level_findings"] = metadata_report.get("document_level_findings", [])
        report["not_evaluated_checks"] = metadata_report.get("not_evaluated_checks", [])
        report["official_records"] = metadata_report.get("official_records", {})
        if "error" in metadata_report:
            report["metadata_error"] = metadata_report["error"]
        return _normalize_library_report(report, reference_entries)
    except Exception as exc:
        traceback.print_exc()
        raise RuntimeError(f"Library pipeline failed: {exc}") from exc


def _source_label(entry: dict[str, Any] | None, number: Any) -> str:
    lead_author, _, year = _reference_identity(entry) if isinstance(entry, dict) else (None, None, None)
    return f"{lead_author} ({year})" if lead_author and year else f"Reference {number}"


def _manifest_sha256(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_intake_manifest(sentences: list[dict[str, Any]], reference_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Freeze the bibliography, cited sentences and orphan markers exactly as the judge sees them."""
    from src.citation_parser import parse_citation_markers
    from src.pipeline import resolve_reference_numbers_for_sentence

    reference_map: dict[int, dict[str, Any]] = {}
    for entry in reference_list:
        try:
            reference_map[int(entry.get("number"))] = entry
        except (TypeError, ValueError, AttributeError):
            continue

    bibliography = [
        {
            "reference_id": number,
            "marker": f"[{number}]",
            "reference": str(entry.get("raw") or "").strip(),
            "doi": entry.get("doi"),
            "source_label": _source_label(entry, number),
        }
        for number, entry in sorted(reference_map.items())
    ]

    citations: list[dict[str, Any]] = []
    orphan_citations: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for sentence in sentences:
        claim = sentence["text"]
        numbers = parse_citation_markers(claim) or resolve_reference_numbers_for_sentence(claim, reference_map)
        for number in numbers:
            key = (number in reference_map, number, claim)
            if key in seen:
                continue
            seen.add(key)
            if number in reference_map:
                citations.append({"reference_id": number, "marker": f"[{number}]", "claim": claim})
            else:
                orphan_citations.append({"marker": f"[{number}]", "claim": claim})
    return {"bibliography": bibliography, "citations": citations, "orphan_citations": orphan_citations}


def _build_metadata_cards(metadata_output: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    reference_dir = str(Path(__file__).resolve().parents[1] / "reference")
    if reference_dir not in sys.path:
        sys.path.insert(0, reference_dir)
    from render_metadata_cards import build_cards_from_metadata

    return build_cards_from_metadata(metadata_output, manifest)


def _assemble_publication_inputs(
    report: dict[str, Any],
    manifest_core: dict[str, Any],
    metadata_output: dict[str, Any],
    *,
    judge_executed: bool,
    simulation: bool,
    metadata_status: str,
) -> dict[str, Any]:
    """Build cases, counts and the final manifest; every DRAFT cause is listed, never hidden."""
    causes: list[str] = []
    if not judge_executed:
        causes.append("the judge did not run")
    if simulation:
        causes.append("simulated judge output cannot be published as FINAL")
    if metadata_status != "ok":
        causes.append(f"metadata stage status is {metadata_status}")
    metadata_failure = metadata_output.get("error") or metadata_output.get("metadata_error")
    if metadata_failure:
        causes.append(f"metadata stage failed: {metadata_failure}")

    for sentence in (report.get("library_pipeline") or {}).get("sentences", []):
        for pair in sentence.get("pairs", []):
            result = pair.get("report_text_result")
            if isinstance(result, dict) and result.get("status") == "report_text_failed":
                causes.append(f"report_text_failed for reference [{pair.get('ref_number')}] in sentence {pair.get('sentence_id')}")

    manifest = copy.deepcopy(manifest_core)
    cases = list(report.get("cases") or [])
    references_reviewed = 0
    try:
        built = _build_metadata_cards(metadata_output, manifest)
        cases.extend(built["cards"])
        references_reviewed = built["references_reviewed"]
        if built["blocks_final"]:
            causes.append(f"unknown reference status blocks FINAL: {built['blocks_final']}")
        if built.get("skipped"):
            causes.append(f"metadata card not rendered, reference never cited in a parsed sentence: {built['skipped']}")
    except Exception as exc:  # fail closed: a missing record or citation is an integration defect
        causes.append(f"metadata cards could not be assembled: {exc}")

    manifest["references_reviewed"] = references_reviewed
    return {
        "cases": cases,
        "references_reviewed": references_reviewed,
        "manifest": manifest,
        "expected_manifest_sha256": _manifest_sha256(manifest),
        "causes": causes,
    }


def _build_simulated_library_report(sentences: list[dict[str, Any]], reference_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Opt-in offline stand-in for the library pipeline, driven by the spec fixtures; never a real verdict."""
    from src.offline_simulation import (fixture_report_text_provider,
                                        simulate_pairs)
    from src.pipeline import _apply_report_text_gate

    reference_map: dict[int, dict[str, Any]] = {}
    for entry in reference_list:
        try:
            reference_map[int(entry.get("number"))] = entry
        except (TypeError, ValueError, AttributeError):
            continue

    pairs = simulate_pairs(sentences, reference_map)
    pairs = _apply_report_text_gate(pairs, None, reference_list, provider=fixture_report_text_provider)
    report = generate_report(pairs)
    report.update({
        "bibliography_status": [],
        "document_level_findings": [],
        "not_evaluated_checks": [],
        "official_records": {},
        "simulation": True,
    })
    normalized = _normalize_library_report(report, reference_list)
    for case in normalized["cases"]:
        case["simulated"] = True
    return normalized


def _report_counts(cases: list[dict[str, Any]], verification_cases: list[dict[str, Any]]) -> dict[str, int]:
    """Distinct references with a confirmed error, and distinct references that need manual verification."""
    def distinct(items: list[dict[str, Any]]) -> int:
        return len({str(c.get("reference_id")) for c in items if c.get("count_as_reference") is not False})

    return {"errors_detected": distinct(cases), "require_verification": distinct(verification_cases)}


def run_orchestrator(source_path: str | Path, out_dir: str | Path | None = None) -> dict[str, Any]:
    source = Path(source_path)
    display_name = source.name
    out_dir_path = Path(out_dir) if out_dir is not None else source.parent / "refcheck_output"
    out_dir_path.mkdir(parents=True, exist_ok=True)
    _atomic_write(out_dir_path / "run_record.json", json.dumps({"status": "running", "source_file": display_name}, indent=2))

    text = _extract_manuscript_text(source)
    if not text.strip():
        raise ValueError(f"No readable text was found in manuscript: {source}")

    parse_result = parse_pasted_text(text)
    citation_numbers = _citation_numbers(text)
    author_year_matches = _author_year_citations(text)

    if parse_result.reference_list and parse_result.sentences:
        reference_list = parse_result.reference_list
        sentences = [
            {"sentence_id": item["id"], "id": item["id"], "text": item["text"], "sentence_text": item["text"]}
            for item in parse_result.sentences
        ]
    else:
        if not citation_numbers and not author_year_matches:
            raise ValueError(f"No numbered or author-year citations were found in manuscript: {source.name}")

        reference_list = parse_result.reference_list or _suggest_reference_entries(text)
        sentences = [
            {"sentence_id": f"s{i}", "id": f"s{i}", "text": sentence.strip(), "sentence_text": sentence.strip()}
            for i, sentence in enumerate([chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+(?=[A-Z\[])", text) if chunk.strip()], start=1)
            if sentence
        ]

    intake_manifest = _build_intake_manifest(sentences, reference_list)
    intake_manifest_sha256 = _manifest_sha256(intake_manifest)

    dataset_rows: list[dict[str, Any]] = []
    for sentence in sentences:
        cited_numbers = _citation_numbers(sentence["text"])
        dataset_rows.append(
            {
                "sentence_id": sentence["id"],
                "sentence_text": sentence["text"],
                "cited_numbers": ", ".join(str(n) for n in cited_numbers),
                "author_year_matches": len(AUTHOR_YEAR_RE.findall(sentence["text"])),
                "reference_count": len(reference_list),
                "source_file": display_name,
                "source_path": str(source),
                "status": "parsed",
            }
        )

    citation_matches = _dedupe_matches_by_identity(
        _enrich_local_matches(_match_citations_to_references(sentences, reference_list), sentences, reference_list)
    )
    citation_matches = _ensure_reference_text_for_matches(citation_matches, reference_list)
    unique_citation_keys = {
        (match.get("sentence_id"), match.get("matched_reference"), match.get("citation"))
        for match in citation_matches
    }
    metadata_report = _build_metadata_report(sentences, reference_list, text)
    simulation_mode = _api_key_missing() and os.getenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "").strip().lower() in {"1", "true", "yes", "on"}
    if simulation_mode:
        library_report = _build_simulated_library_report(sentences, reference_list)
    else:
        library_report = None if _api_key_missing() else _build_library_report(sentences, reference_list, text)
    metadata_status = "ok"
    if "error" in metadata_report:
        metadata_status = "error"
    elif _api_key_missing():
        metadata_status = "not_evaluated"

    judge_details = _judge_execution_state(
        api_key_missing=_api_key_missing(),
        simulation_mode=simulation_mode,
        library_report=library_report,
    )

    report = {
        "source_file": display_name,
        "source_path": str(source),
        "source_type": source.suffix.lower().lstrip("."),
        "status": "ok",
        "file_status": "ok",
        "judge_executed": judge_details["judge_executed"],
        "judge_ran": judge_details["judge_executed"],
        "judge_status": judge_details["judge_status"],
        "judge_blocker": judge_details["judge_blocker"],
        "judge_prompt_version": judge_details["judge_prompt_version"],
        "judge_prompt_hash": judge_details["judge_prompt_hash"],
        "provider_attempts": judge_details["provider_attempts"],
        "models_called": judge_details.get("models_called", []),
        "luna_calls": int(judge_details.get("luna_calls", 0) or 0),
        "sol_calls": int(judge_details.get("sol_calls", 0) or 0),
        "judge_simulated": simulation_mode,
        "metadata_status": metadata_status,
        "final_verdict_status": judge_details["final_verdict_status"],
        "final_verdict": judge_details["final_verdict"],
        "scientific_verdict": judge_details["scientific_verdict"],
        "judgment_provenance": judge_details["verdict_provenance"],
        "simulation": simulation_mode,
        "simulation_mode": "offline_mock" if simulation_mode else "live_or_configured",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sentences": sentences,
        "references": reference_list,
        "citation_matches": citation_matches,
        "citation_count": len(unique_citation_keys),
        "dataset_rows": dataset_rows,
        "summary": {
            "prepared_for": "Document review",
            "issued_by": "RefCheck",
            "simulation": simulation_mode,
        },
    }

    if metadata_report.get("bibliography_status") or metadata_report.get("document_level_findings") or metadata_report.get("not_evaluated_checks") or metadata_report.get("official_records"):
        report["bibliography_status"] = metadata_report.get("bibliography_status", [])
        report["document_level_findings"] = metadata_report.get("document_level_findings", [])
        report["not_evaluated_checks"] = metadata_report.get("not_evaluated_checks", [])
        report["official_records"] = metadata_report.get("official_records", {})
        if "error" in metadata_report:
            report["metadata_error"] = metadata_report["error"]

    if library_report is not None:
        report["library_pipeline"] = library_report
        report["cases"] = library_report.get("cases", [])
        report["lang"] = "en"
        report["report_language"] = "en"
        report["summary"]["library_pipeline_used"] = True
        report["sentences"] = library_report.get("sentences", sentences)
        library_matches = _dedupe_matches_by_identity(library_report.get("citation_matches") or [])
        final_matches = library_matches if library_matches else citation_matches
        report["citation_matches"] = _ensure_reference_text_for_matches(final_matches, reference_list)
        report["citation_count"] = len({
            (match.get("sentence_id"), match.get("matched_reference"), match.get("citation"))
            for match in final_matches
        })
        report["summary"]["total_sentences"] = library_report.get("summary", {}).get("total_sentences", len(sentences))
        report["summary"]["total_checks"] = library_report.get("summary", {}).get("total_checks", 0)
        if not library_matches:
            print("[DEBUG] Real library pipeline returned no detailed matches; keeping the local matches found in the manuscript.")
        else:
            print("[DEBUG] Using real library pipeline for citation judging.")
            _print_llm_judge_results(library_report)
    else:
        print("[DEBUG] No real library pipeline was used; falling back to local parsing logic.")

    report_json_path = out_dir_path / "report.json"
    html_path = out_dir_path / "report.html"
    excel_path = out_dir_path / "dataset.xlsx"
    run_record_path = out_dir_path / "run_record.json"

    # Use the metadata run that decided show_content, so records and cards share one resolution.
    metadata_output = library_report if library_report is not None and library_report.get("bibliography_status") is not None else metadata_report
    official_records = metadata_output.get("official_records") if isinstance(metadata_output.get("official_records"), dict) else {}
    report["official_records"] = official_records
    for key in ("bibliography_status", "document_level_findings", "not_evaluated_checks"):
        if key in metadata_output:
            report[key] = metadata_output[key]

    assembled = _assemble_publication_inputs(
        report,
        intake_manifest,
        {**metadata_output, "official_records": official_records},
        judge_executed=bool(judge_details["judge_executed"]),
        simulation=simulation_mode,
        metadata_status=metadata_status,
    )
    manifest = assembled["manifest"]
    expected_manifest_sha256 = assembled["expected_manifest_sha256"]
    causes = list(assembled["causes"])
    core = {key: value for key, value in manifest.items() if key != "references_reviewed"}
    if _manifest_sha256(core) != intake_manifest_sha256:
        causes.append("manifest changed after intake")

    report["lang"] = "en"
    report["report_language"] = "en"
    report["cases"] = assembled["cases"]
    report["references_reviewed"] = assembled["references_reviewed"]
    report["verification_cases"] = list((library_report or {}).get("verification_cases") or [])
    report["uncertain_pairs"] = list((library_report or {}).get("uncertain_pairs") or [])
    report["counts"] = _report_counts(report["cases"], report["verification_cases"])
    report["manifest"] = manifest
    report["intake_manifest_sha256"] = intake_manifest_sha256
    report["expected_manifest_sha256"] = expected_manifest_sha256

    publication = _apply_publication_gate(report, manifest, official_records, expected_manifest_sha256, extra_errors=causes)
    report["report_complete"] = publication["report_complete"]
    report["publication_status"] = publication["publication_status"]
    report["publication_errors"] = publication.get("publication_errors", [])
    report["publication_reason"] = publication.get("publication_reason", "")

    _atomic_write(report_json_path, json.dumps(report, ensure_ascii=False, indent=2))
    _atomic_write(html_path, _build_html_report(report))
    _write_excel(dataset_rows, excel_path)
    judge_log_path = out_dir_path / "judge_log.jsonl"
    _atomic_write(judge_log_path, "".join(
        json.dumps(row, ensure_ascii=False) + "\n" for row in ((library_report or {}).get("call_log") or [])
    ))

    run_record = {
        "status": "ok",
        "file_status": "ok",
        "source_file": display_name,
        "source_path": str(source),
        "generated_at": report["generated_at"],
        "judge_ran": judge_details["judge_executed"],
        "judge_executed": judge_details["judge_executed"],
        "judge_simulated": simulation_mode,
        "simulation": simulation_mode,
        "simulation_mode": "offline_mock" if simulation_mode else "live_or_configured",
        "judge_status": judge_details["judge_status"],
        "judge_blocker": judge_details["judge_blocker"],
        "judge_prompt_version": judge_details["judge_prompt_version"],
        "judge_prompt_hash": judge_details["judge_prompt_hash"],
        "provider_attempts": judge_details["provider_attempts"],
        "models_called": judge_details.get("models_called", []),
        "luna_calls": int(judge_details.get("luna_calls", 0) or 0),
        "sol_calls": int(judge_details.get("sol_calls", 0) or 0),
        "metadata_status": metadata_status,
        "final_verdict_status": judge_details["final_verdict_status"],
        "final_verdict": judge_details["final_verdict"],
        "scientific_verdict": judge_details["scientific_verdict"],
        "judgment_provenance": judge_details["verdict_provenance"],
        "publication_status": publication["publication_status"],
        "publication_errors": publication.get("publication_errors", []),
        "publication_reason": publication.get("publication_reason", ""),
        "references_reviewed": report["references_reviewed"],
        "counts": report["counts"],
        "verification_cases": report["verification_cases"],
        "uncertain_pairs": report["uncertain_pairs"],
        "intake_manifest_sha256": intake_manifest_sha256,
        "expected_manifest_sha256": expected_manifest_sha256,
        "output_files": {
            "report_json": report_json_path.name,
            "excel_dataset": excel_path.name,
            "run_record": run_record_path.name,
            "judge_log": judge_log_path.name,
        },
    }
    _atomic_write(run_record_path, json.dumps(run_record, ensure_ascii=False, indent=2))

    return {
        "status": "ok",
        "file_status": "ok",
        "source_file": display_name,
        "source_path": str(source),
        "report_json": str(report_json_path),
        "html_report": str(html_path),
        "excel_dataset": str(excel_path),
        "run_record": str(run_record_path),
        "judge_executed": judge_details["judge_executed"],
        "judge_status": judge_details["judge_status"],
        "judge_blocker": judge_details["judge_blocker"],
        "judge_prompt_version": judge_details["judge_prompt_version"],
        "judge_prompt_hash": judge_details["judge_prompt_hash"],
        "provider_attempts": judge_details["provider_attempts"],
        "metadata_status": metadata_status,
        "final_verdict_status": judge_details["final_verdict_status"],
        "final_verdict": judge_details["final_verdict"],
        "scientific_verdict": judge_details["scientific_verdict"],
        "judgment_provenance": judge_details["verdict_provenance"],
    }
