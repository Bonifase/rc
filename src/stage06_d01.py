"""Stage 1 / D-01 claim-adapter validation for the PP-01 manuscript fixture.

This implements the T-06 acceptance gate in the same deterministic, fixture-driven
style as T-05, but it exercises the Stage 1 adapter/reconciliation contract:

- it identifies cited claim sentences from the manuscript body
- it normalizes them into claim objects with their cited reference numbers
- it resolves each citation to the corresponding bibliography record
- it validates that every claim can be reconciled to a real reference

The goal is to separate the claim adapter from the UI, keeping the verification
pipeline deterministic and replayable.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.citation_parser import parse_citation_markers
from src.text_parser import parse_pasted_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage06_fixture.json"

STAGE06_RULES = [
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


def _extract_reference_records(text: str) -> dict[int, str]:
    """Return {reference_number: full_reference_text} for bibliography blocks."""
    lines = text.splitlines()
    start_idx = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^\s*(references|bibliography|works cited|literature cited|citations)\s*$", stripped, re.IGNORECASE):
            start_idx = idx + 1
            break
        if re.match(r"^\s*\[(\d+)\]\s+", stripped) or re.match(r"^\s*(\d+)\s*[\.)]\s+", stripped):
            start_idx = idx
            break

    if start_idx is None:
        return {}

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^\s*\[(\d+)\]\s+", stripped) or re.match(r"^\s*(\d+)\s*[\.)]\s+", stripped):
            if current:
                blocks.append(current)
            current = [line]
            continue
        if current:
            current.append(line)
    if current:
        blocks.append(current)

    records: dict[int, str] = {}
    for block in blocks:
        first = block[0].strip()
        match = re.match(r"^\s*\[(\d+)\]\s+", first)
        if not match:
            match = re.match(r"^\s*(\d+)\s*[\.)]\s+", first)
        if not match:
            continue
        number = int(match.group(1))
        records[number] = " ".join(part.strip() for part in block if part.strip())
    return records


def _build_reconciled_claims(parse_result: Any, manuscript_text: str) -> list[dict[str, Any]]:
    reference_map = {int(entry["number"]): entry for entry in parse_result.reference_list if "number" in entry}
    raw_ref_records = _extract_reference_records(manuscript_text)

    claims: list[dict[str, Any]] = []
    seen_sentence_texts: set[str] = set()

    for index, sentence in enumerate(parse_result.sentences, start=1):
        sentence_text = sentence.get("text", "").strip()
        if not sentence_text:
            continue
        cited_numbers = parse_citation_markers(sentence_text)
        if not cited_numbers:
            continue
        if sentence_text in seen_sentence_texts:
            continue
        seen_sentence_texts.add(sentence_text)

        matched: list[dict[str, Any]] = []
        for ref_number in cited_numbers:
            reference_record = reference_map.get(ref_number)
            if reference_record is None:
                matched.append({
                    "number": ref_number,
                    "doi": None,
                    "record": raw_ref_records.get(ref_number),
                    "resolved": False,
                })
                continue

            matched.append({
                "number": ref_number,
                "doi": reference_record.get("doi"),
                "record": raw_ref_records.get(ref_number) or reference_record,
                "resolved": True,
            })

        claim = {
            "claim_id": f"claim-{index}",
            "sentence": sentence_text,
            "reference_numbers": cited_numbers,
            "matched_reference_records": matched,
            "reconciled": all(item["resolved"] for item in matched),
        }
        claims.append(claim)

    return claims


def validate_stage06_pp01_fixture(text: str | None = None, fixture_path: str | Path | None = None) -> dict[str, Any]:
    """Validate the Stage 1 claim-adapter reconciliation of the PP-01 fixture.

    Returns a dict with:
      - status: "pass" | "fail"
      - rules: {rule_id: bool}
      - passed_rules / failed_rules
      - checked_sentence_count / checked_reference_count
      - reconciled_claims
      - fixture_id
    """
    fixture = _load_fixture(fixture_path)
    manuscript_text = text if text is not None else fixture["manuscript_text"]
    parse_result = parse_pasted_text(manuscript_text)

    sentence_texts = [entry["text"] for entry in parse_result.sentences]
    reference_numbers = [int(entry["number"]) for entry in parse_result.reference_list if "number" in entry]
    reconciled_claims = _build_reconciled_claims(parse_result, manuscript_text)
    expected = fixture.get("expected", {})

    rules: dict[str, bool] = {}
    rules["R-01"] = len(sentence_texts) >= 1
    rules["R-02"] = bool(parse_result.reference_list)
    rules["R-03"] = bool(reconciled_claims)
    rules["R-04"] = all(bool(claim["reference_numbers"]) for claim in reconciled_claims)
    rules["R-05"] = all(
        set(claim["reference_numbers"]).issubset(set(reference_numbers))
        for claim in reconciled_claims
    )
    rules["R-06"] = (
        expected.get("claim_count") is None
        or len(reconciled_claims) == expected["claim_count"]
    )
    rules["R-07"] = len({claim["claim_id"] for claim in reconciled_claims}) == len(reconciled_claims)
    rules["R-08"] = all(
        len(claim["reference_numbers"]) == len(set(claim["reference_numbers"]))
        for claim in reconciled_claims
    )
    rules["R-09"] = all(claim["reconciled"] for claim in reconciled_claims)
    rules["R-10"] = (
        (expected.get("sentence_count") is None or len(sentence_texts) == expected["sentence_count"])
        and (expected.get("reference_count") is None or len(parse_result.reference_list) == expected["reference_count"])
        and (expected.get("reconciled_claim_count") is None or len(reconciled_claims) == expected["reconciled_claim_count"])
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
        "matched_citation_numbers": sorted({n for claim in reconciled_claims for n in claim["reference_numbers"]}),
        "reference_numbers": reference_numbers,
        "reconciled_claims": reconciled_claims,
        "warnings": parse_result.warnings,
    }


__all__ = ["STAGE06_RULES", "validate_stage06_pp01_fixture"]
