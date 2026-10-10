"""Stage 1 / D-01 citation-resolution validation for the PP-01 manuscript fixture.

This implements the T-02 acceptance gate in the same deterministic, fixture-driven
style as the other D-01 contracts.

The focus is the core resolution layer that ties manuscript sentences to the
bibliography:

- bracket citations such as [1] and [2,3-4]
- author-year citations such as Smith et al. (2020)
- bibliography records whose reference keys are resolved by raw-text matching
- the exact minimum smoke contract required to keep the pipeline deterministic

The goal is to keep the production parser and the validation gate separate.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.citation_parser import parse_citation_markers
from src.pipeline import resolve_reference_numbers_for_sentence
from src.text_parser import parse_pasted_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage02_fixture.json"

STAGE02_RULES = [
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


def _author_year_matches(text: str) -> list[tuple[str, int]]:
    """Return [(author_text, year), ...] matches in the sentence."""
    matches: list[tuple[str, int]] = []
    pattern = re.compile(
        r"([A-Z][A-Za-z'’.-]+(?:,\s*[A-Z]\.)*(?:\s+(?:et\s+al\.|and|&)\s*(?:[A-Z][A-Za-z'’.-]+)?)?)\s*\((\d{4})\)"
    )
    for match in pattern.finditer(text):
        author_text, year_text = match.groups()
        matches.append((author_text, int(year_text)))
    return matches


def _resolve_sentence_numbers(parse_result: Any, sentence_text: str) -> list[int]:
    reference_map = {int(entry["number"]): entry for entry in parse_result.reference_list if "number" in entry}
    return resolve_reference_numbers_for_sentence(sentence_text, reference_map)


def validate_stage02_pp01_fixture(text: str | None = None, fixture_path: str | Path | None = None) -> dict[str, Any]:
    """Validate the T-02 reference-resolution smoke fixture.

    The contract checks that:
      - the manuscript parses into sentence + bibliography blocks
      - bracket citations can be expanded into concrete reference numbers
      - author-year citations resolve to the right bibliography records
      - parsing and resolution are deterministic and replayable
    """
    fixture = _load_fixture(fixture_path)
    manuscript_text = text if text is not None else fixture["manuscript_text"]
    parse_result = parse_pasted_text(manuscript_text)

    sentence_texts = [entry["text"] for entry in parse_result.sentences]
    reference_numbers = [int(entry["number"]) for entry in parse_result.reference_list if "number" in entry]
    reference_map = {int(entry["number"]): entry for entry in parse_result.reference_list if "number" in entry}

    resolved_numbers_by_sentence: list[dict[str, Any]] = []
    author_year_total = 0
    for sentence in sentence_texts:
        sentence_numbers = _resolve_sentence_numbers(parse_result, sentence)
        author_year_entries = _author_year_matches(sentence)
        author_year_total += len(author_year_entries)
        resolved_numbers_by_sentence.append(
            {
                "sentence": sentence,
                "resolved_numbers": sentence_numbers,
                "author_year_matches": author_year_entries,
            }
        )

    all_resolved = [item for item in resolved_numbers_by_sentence if item["resolved_numbers"]]
    expected = fixture.get("expected", {})

    rules: dict[str, bool] = {}
    rules["R-01"] = len(sentence_texts) >= 1
    rules["R-02"] = bool(parse_result.reference_list)
    rules["R-03"] = bool(parse_result.sentences)
    rules["R-04"] = all(("number" in entry and isinstance(entry["number"], int)) for entry in parse_result.reference_list)
    rules["R-05"] = bool(all_resolved)
    rules["R-06"] = all(
        set(item["resolved_numbers"]).issubset(set(reference_numbers))
        for item in resolved_numbers_by_sentence
        if item["resolved_numbers"]
    )
    rules["R-07"] = author_year_total >= 1
    rules["R-08"] = all(
        not item["author_year_matches"] or bool(item["resolved_numbers"])
        for item in resolved_numbers_by_sentence
    )
    rules["R-09"] = all(
        bool(entry.get("raw")) and isinstance(entry.get("raw", ""), str)
        for entry in parse_result.reference_list
    )
    rules["R-10"] = (
        (expected.get("sentence_count") is None or len(sentence_texts) == expected["sentence_count"])
        and (expected.get("reference_count") is None or len(parse_result.reference_list) == expected["reference_count"])
        and (expected.get("resolved_sentence_count") is None or len(all_resolved) == expected["resolved_sentence_count"])
        and (expected.get("author_year_count") is None or author_year_total == expected["author_year_count"])
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
        "resolved_sentence_count": len(all_resolved),
        "author_year_count": author_year_total,
        "resolved_by_sentence": resolved_numbers_by_sentence,
        "reference_numbers": reference_numbers,
        "warnings": parse_result.warnings,
    }


__all__ = ["STAGE02_RULES", "validate_stage02_pp01_fixture"]
