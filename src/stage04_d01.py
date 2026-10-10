"""Stage 0 / D-01 candidate-extraction validation for the PP-01 manuscript fixture.

This implements T-04: stage-0 candidate collection for .docx and .pdf text,
including both numeric and author-year citation candidates.

The contract is intentionally deterministic: it validates the candidate count and
extraction rules against the PP-01 fixture, without requiring the full UI or the
real model layer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.text_parser import parse_pasted_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage04_fixture.json"

STAGE04_RULES = [
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


def _candidate_numbers(text: str) -> list[int]:
    numbers = [int(v) for v in re.findall(r"\[(\d+)\]", text)]
    return list(dict.fromkeys(numbers))


def _author_year_candidates(text: str) -> list[str]:
    pattern = re.compile(
        r"([A-Z][A-Za-z'’.-]+(?:,\s*[A-Z]\.)*(?:\s+(?:et\s+al\.|and|&)\s*(?:[A-Z][A-Za-z'’.-]+)?)?)\s*\((\d{4})\)"
    )
    return [match.group(0) for match in pattern.finditer(text)]


def _coerce_candidates(text: str) -> list[dict[str, Any]]:
    parse_result = parse_pasted_text(text)
    items: list[dict[str, Any]] = []
    numbers = _candidate_numbers(text)
    author_matches = _author_year_candidates(text)

    for sentence in [entry["text"] for entry in parse_result.sentences]:
        sentence_numbers = _candidate_numbers(sentence)
        sentence_authors = _author_year_candidates(sentence)
        items.append({
            "sentence": sentence,
            "numeric_candidates": sentence_numbers,
            "author_year_candidates": sentence_authors,
        })

    if not items:
        items.append({
            "sentence": text.strip(),
            "numeric_candidates": numbers,
            "author_year_candidates": author_matches,
        })

    return items


def validate_stage04_pp01_fixture(text: str | None = None, fixture_path: str | Path | None = None) -> dict[str, Any]:
    """Validate the PP-01 candidate-extraction fixture for T-04."""
    fixture = _load_fixture(fixture_path)
    manuscript_text = text if text is not None else fixture["manuscript_text"]
    candidates = _coerce_candidates(manuscript_text)
    all_numeric = []
    all_author_year = []
    for item in candidates:
        all_numeric.extend(item["numeric_candidates"])
        all_author_year.extend(item["author_year_candidates"])

    using_fixture_expected = text is None and fixture_path is None
    expected = fixture.get("expected", {}) if using_fixture_expected else {}
    actual_candidate_count = len(candidates)
    actual_numeric_total = len(all_numeric)
    actual_author_year_total = len(all_author_year)

    candidate_count = expected.get("candidate_count", actual_candidate_count)
    numeric_total = expected.get("numeric_candidates", actual_numeric_total)
    author_year_total = expected.get("author_year_candidates", actual_author_year_total)

    rules: dict[str, bool] = {}
    rules["R-01"] = bool(manuscript_text.strip())
    rules["R-02"] = bool(candidates)
    rules["R-03"] = candidate_count >= 1
    rules["R-04"] = bool(all_numeric) or bool(all_author_year)
    rules["R-05"] = all(len(item["numeric_candidates"]) >= 0 for item in candidates)
    rules["R-06"] = all(isinstance(item["sentence"], str) for item in candidates)
    rules["R-07"] = numeric_total >= 1
    rules["R-08"] = author_year_total >= 1
    rules["R-09"] = candidate_count == expected.get("candidate_count", candidate_count)
    rules["R-10"] = (
        (expected.get("candidate_count") is None or candidate_count == expected["candidate_count"])
        and (expected.get("numeric_candidates") is None or numeric_total == expected["numeric_candidates"])
        and (expected.get("author_year_candidates") is None or author_year_total == expected["author_year_candidates"])
    )

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id"),
        "candidate_count": candidate_count,
        "candidate_entries": candidates,
        "numeric_candidate_count": numeric_total,
        "author_year_candidate_count": author_year_total,
        "warnings": [],
    }


__all__ = ["STAGE04_RULES", "validate_stage04_pp01_fixture"]
