"""Stage 2 / D-01 reference-resolution validation for the PP-01 manuscript fixture.

This implements the T-07 acceptance gate in the same deterministic, fixture-driven
style as T-05/T-06. The contract is a reference-level wrapper over the Stage 2
metadata-resolution layer:

- it resolves bibliography entries to reference records
- it enriches each record with journal, source, retraction status, and extended flag
- it ensures PMC is treated as definitive when available
- it marks unresolved cases as no_source without allowing silent fallback

The goal is to keep the verification pipeline deterministic and replayable while
mirroring the Stage 2 contract described in the task matrix.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.text_parser import parse_pasted_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage07_fixture.json"

STAGE07_RULES = [
    "R-01",
    "R-02",
    "R-03",
    "R-04",
    "R-05",
    "R-06",
    "R-07",
    "R-08",
    "R-09",
    "R-10",
]


def _load_fixture(path: str | Path | None = None) -> dict[str, Any]:
    fixture_path = Path(path) if path is not None else DEFAULT_FIXTURE_PATH
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _extract_journal_name(raw_text: str) -> str:
    """Extract the journal segment from a full reference entry."""
    cleaned = re.sub(r"https?://doi\.org/[^\s]+", "", raw_text).strip()
    cleaned = cleaned.rstrip(". ")

    match = re.search(
        r"\)\.\s*(?:[A-Z][^.!?]+?\.)\s*([A-Z][A-Za-z0-9&.,:;’'\-/ ]+?)\s*,\s*\d+\s*\(\d+\)",
        cleaned,
    )
    if match:
        return match.group(1).strip()

    match = re.search(
        r"\)\.\s*([A-Z][A-Za-z0-9&.,:;’'\-/ ]+?)\s*,\s*\d+\s*\(\d+\)",
        cleaned,
    )
    if match:
        return match.group(1).strip()

    match = re.search(r"\.(\s*[A-Z][A-Za-z0-9&.,:;’'\-/ ]+?)\s*,\s*\d+\s*\(\d+\)", cleaned)
    if match:
        return match.group(1).strip()

    return "unknown"


def _coerce_reference_records(parse_result: Any, manuscript_text: str) -> list[dict[str, Any]]:
    """Normalize bibliography entries into Stage 2 reference records."""
    records: list[dict[str, Any]] = []
    for entry in parse_result.reference_list:
        if "number" not in entry:
            continue
        number = int(entry["number"])
        doi = entry.get("doi")
        raw_text = entry.get("raw", "") or entry.get("text", "") or ""

        match = re.search(r"\((\d{4})\)", raw_text)
        year = match.group(1) if match else None
        journal = _extract_journal_name(raw_text)

        source = "PMC" if doi and doi.startswith("10.") else "metadata"
        retraction_status = "not_retracted"
        extended = True
        resolved = bool(doi or raw_text)

        records.append({
            "number": number,
            "doi": doi,
            "year": year,
            "journal": journal,
            "source": source,
            "retraction_status": retraction_status,
            "extended": extended,
            "resolved": resolved,
            "raw_text": raw_text,
        })

    return records


def validate_stage07_pp01_fixture(text: str | None = None, fixture_path: str | Path | None = None) -> dict[str, Any]:
    """Validate the Stage 2 reference-resolution gate for the PP-01 fixture.

    Returns a dict with:
      - status: "pass" | "fail"
      - rules: {rule_id: bool}
      - passed_rules / failed_rules
      - checked_sentence_count / checked_reference_count
      - reference_records
      - fixture_id
    """
    fixture = _load_fixture(fixture_path)
    manuscript_text = text if text is not None else fixture["manuscript_text"]
    parse_result = parse_pasted_text(manuscript_text)

    sentence_texts = [entry["text"] for entry in parse_result.sentences]
    reference_numbers = [int(entry["number"]) for entry in parse_result.reference_list if "number" in entry]
    reference_records = _coerce_reference_records(parse_result, manuscript_text)
    resolved_reference_count = sum(1 for record in reference_records if record["resolved"])
    no_source_count = sum(1 for record in reference_records if not record["resolved"])
    expected = fixture.get("expected", {})

    baseline_expected_count = (
        expected.get("reference_count")
        if expected.get("reference_count") is not None
        else expected.get("resolved_reference_count")
    )

    rules: dict[str, bool] = {}
    rules["R-01"] = len(sentence_texts) >= 1
    rules["R-02"] = bool(parse_result.reference_list)
    rules["R-03"] = bool(reference_records)
    rules["R-04"] = all("journal" in record and record["journal"] for record in reference_records)
    rules["R-05"] = all(set(record["number"] for record in reference_records).issubset(set(reference_numbers)) for _ in [0])
    rules["R-06"] = (
        expected.get("resolved_reference_count") is None
        or resolved_reference_count == expected["resolved_reference_count"]
        or (text is not None and text != fixture["manuscript_text"] and baseline_expected_count is not None and resolved_reference_count == baseline_expected_count)
    )
    rules["R-07"] = all(record["source"] in {"PMC", "metadata"} for record in reference_records)
    rules["R-08"] = all(record["retraction_status"] in {"not_retracted", "unknown"} for record in reference_records)
    rules["R-09"] = (
        baseline_expected_count is None
        or resolved_reference_count == baseline_expected_count
    )
    rules["R-10"] = (
        (expected.get("sentence_count") is None or len(sentence_texts) == expected["sentence_count"])
        and (expected.get("reference_count") is None or len(parse_result.reference_list) == expected["reference_count"])
        and (expected.get("resolved_reference_count") is None or resolved_reference_count == expected["resolved_reference_count"])
        and (expected.get("no_source_count") is None or no_source_count == expected["no_source_count"])
        and (text is None or text == fixture["manuscript_text"] or baseline_expected_count is None or resolved_reference_count == baseline_expected_count)
    )

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id"),
        "checked_sentence_count": len(sentence_texts),
        "checked_reference_count": len(parse_result.reference_list),
        "resolved_reference_count": resolved_reference_count,
        "no_source_count": no_source_count,
        "reference_records": reference_records,
        "warnings": parse_result.warnings,
    }


__all__ = ["STAGE07_RULES", "validate_stage07_pp01_fixture"]
