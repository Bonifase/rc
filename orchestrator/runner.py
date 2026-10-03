from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pandas as pd

from src.aggregator import generate_report
from src.analysis_runner import run_content_and_metadata
from src.config import OPENAI_API_KEY, require_contact_email
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


def _match_citations_to_references(sentences: list[dict[str, Any]], references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    ref_by_number = {int(ref.get("number")): ref for ref in references if isinstance(ref.get("number"), int)}
    ref_by_number.update({int(ref.get("number")): ref for ref in references if isinstance(ref.get("number"), str) and ref.get("number").isdigit()})

    for sentence in sentences:
        text = sentence.get("text", "")

        for match in NUMBERED_CITATION_RE.finditer(text):
            inner = match.group(1)
            for part in re.split(r"\s*(?:,|-)\s*", inner.strip()):
                if not part:
                    continue
                try:
                    ref_number = int(part)
                except ValueError:
                    continue
                matched_number = ref_by_number.get(ref_number)
                matches.append(
                    {
                        "sentence_id": sentence.get("sentence_id", sentence.get("id", "")),
                        "citation": f"[{part}]",
                        "matched_reference": ref_number if matched_number is not None else None,
                        "match_rule": "numbered_reference_number",
                        "status": "matched" if matched_number is not None else "unmatched",
                    }
                )

        for regex in (AUTHOR_YEAR_RE, AUTHOR_YEAR_PAREN_RE, AUTHOR_YEAR_PLAIN_RE):
            for author_match in regex.finditer(text):
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
                matches.append(
                    {
                        "sentence_id": sentence.get("sentence_id", sentence.get("id", "")),
                        "citation": citation_text,
                        "matched_reference": matched_ref,
                        "match_rule": "author_year_lead_author_and_year",
                        "status": "matched" if matched_ref is not None else "unmatched",
                    }
                )

    return matches


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


def _normalize_library_report(report: dict[str, Any]) -> dict[str, Any]:
    """Convert the LLM report into the flat match schema the browser UI expects."""
    normalized = dict(report)
    sentence_entries = normalized.get("sentences", [])
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str | None, int | str | None]] = set()

    for sentence in sentence_entries:
        sentence_id = sentence.get("sentence_id") or sentence.get("id")
        sentence_text = sentence.get("sentence_text") or sentence.get("text") or ""
        for pair in sentence.get("pairs", []):
            ref_number = pair.get("ref_number")
            if ref_number is None:
                continue
            key = (sentence_id, ref_number)
            if key in seen:
                continue
            seen.add(key)

            raw_pair_status = str(pair.get("status") or "").strip().rstrip(".")
            pair_status = raw_pair_status or "issue"
            verdict = str(pair.get("verdict") or "").lower()
            if pair_status == "verdict":
                status = "matched" if verdict == "supported" else "issue"
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

            matches.append({
                "sentence_id": sentence_id,
                "citation": f"[{ref_number}]" if isinstance(ref_number, int) else str(ref_number),
                "status": status,
                "matched_reference": ref_number,
                "match_rule": match_rule,
                "kind": "content",
                "verdict": verdict,
                "primary_family": pair.get("primary_family"),
                "secondary_family": pair.get("secondary_family"),
                "source_type": pair.get("source_type"),
                "explanation": pair.get("explanation"),
                "evidence_quote": pair.get("evidence_quote"),
                "assertions_checked": pair.get("assertions_checked"),
            })

    normalized["citation_matches"] = matches
    normalized["summary"] = dict(normalized.get("summary", {}))
    normalized["summary"]["library_pipeline_used"] = True
    normalized["citation_count"] = len(matches)
    return normalized


def _build_library_report(sentences: list[dict[str, Any]], reference_list: list[dict[str, Any]], body_text: str) -> dict[str, Any] | None:
    """Use the existing library pipeline when configured; otherwise return None and fall back locally."""
    if not OPENAI_API_KEY:
        print("[DEBUG] Library pipeline skipped: OPENAI_API_KEY is not set.")
        return None

    try:
        contact_email = require_contact_email()
    except RuntimeError as exc:
        print(f"[DEBUG] Library pipeline skipped: {exc}")
        return None

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
        if "error" in metadata_report:
            report["metadata_error"] = metadata_report["error"]
        return _normalize_library_report(report)
    except Exception:
        return None


def run_orchestrator(source_path: str | Path, out_dir: str | Path | None = None) -> dict[str, Any]:
    source = Path(source_path)
    display_name = source.name
    out_dir_path = Path(out_dir) if out_dir is not None else source.parent / "refcheck_output"
    out_dir_path.mkdir(parents=True, exist_ok=True)

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

    citation_matches = _match_citations_to_references(sentences, reference_list)
    unique_citation_keys = {
        (match.get("sentence_id"), match.get("matched_reference"), match.get("citation"))
        for match in citation_matches
    }
    library_report = _build_library_report(sentences, reference_list, text)

    report = {
        "source_file": display_name,
        "source_path": str(source),
        "source_type": source.suffix.lower().lstrip("."),
        "status": "ok",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sentences": sentences,
        "references": reference_list,
        "citation_matches": citation_matches,
        "citation_count": len(unique_citation_keys),
        "dataset_rows": dataset_rows,
        "summary": {
            "prepared_for": "Document review",
            "issued_by": "RefCheck",
        },
    }

    if library_report is not None:
        report["library_pipeline"] = library_report
        report["summary"]["library_pipeline_used"] = True
        report["sentences"] = library_report.get("sentences", sentences)
        library_matches = library_report.get("citation_matches") or []
        final_matches = library_matches if library_matches else citation_matches
        report["citation_matches"] = final_matches
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

    report_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_build_html_report(report), encoding="utf-8")
    _write_excel(dataset_rows, excel_path)

    run_record = {
        "status": "ok",
        "source_file": display_name,
        "source_path": str(source),
        "generated_at": report["generated_at"],
        "output_files": {
            "report_json": report_json_path.name,
            "excel_dataset": excel_path.name,
            "run_record": run_record_path.name,
        },
    }
    run_record_path.write_text(json.dumps(run_record, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "status": "ok",
        "source_file": display_name,
        "source_path": str(source),
        "report_json": str(report_json_path),
        "html_report": str(html_path),
        "excel_dataset": str(excel_path),
        "run_record": str(run_record_path),
    }
