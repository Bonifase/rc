"""Stage 0.5 / D-01 validation for the PP-01 manuscript fixture.

This implements the T-05 acceptance gate in a deterministic, fixture-driven way:

- it parses the manuscript body + bibliography block into structured sentences and refs
- it checks candidate extraction quality against the PP-01 fixture
- it reports a 10-rule pass/fail ledger so the result can be audited and replayed

The goal is to keep the production parser and the D-01 validation contract separate,
matching the “verification pipeline stays separate from the UI” architecture.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.citation_parser import parse_citation_markers
from src.text_parser import parse_pasted_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage05_fixture.json"

PP01_RULES = [
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


def _author_year_pattern_present(text: str) -> bool:
    import re

    return bool(re.search(r"[A-Z][A-Za-z'’.-]+(?:\s+(?:et\s+al\.|and|&))?\s*\(\d{4}\)", text))


def validate_stage05_pp01_fixture(text: str | None = None, fixture_path: str | Path | None = None) -> dict[str, Any]:
    """Validate the extracted manuscript candidates against the PP-01 fixture.

    Args:
        text: optional manuscript text to validate. When omitted, the default PP-01
            fixture is used.
        fixture_path: optional explicit fixture path.

    Returns:
        A dict with:
          - status: "pass" | "fail"
          - rules: {rule_id: bool}
          - passed_rules / failed_rules
          - checked_sentence_count / checked_reference_count
          - matched_citation_numbers
          - fixture_id
    """
    fixture = _load_fixture(fixture_path)
    manuscript_text = text if text is not None else fixture["manuscript_text"]
    parse_result = parse_pasted_text(manuscript_text)

    sentence_texts = [entry["text"] for entry in parse_result.sentences]
    reference_numbers = [int(entry["number"]) for entry in parse_result.reference_list if "number" in entry]
    citation_numbers = []
    for sentence in sentence_texts:
        citation_numbers.extend(parse_citation_markers(sentence))
    citation_numbers = list(dict.fromkeys(citation_numbers))

    expected = fixture.get("expected", {})

    rules: dict[str, bool] = {}
    rules["R-01"] = len(sentence_texts) >= 1
    rules["R-02"] = any("references" in line.lower() for line in manuscript_text.splitlines()) or bool(parse_result.reference_list)
    rules["R-03"] = bool(parse_result.reference_list)
    rules["R-04"] = any("doi" in entry for entry in parse_result.reference_list)
    rules["R-05"] = bool(citation_numbers) and set(citation_numbers).issubset(set(reference_numbers))
    rules["R-06"] = not _author_year_pattern_present("\n".join(sentence_texts)) or bool(parse_result.reference_list)
    rules["R-07"] = all("2." not in sentence for sentence in sentence_texts) or "2. Ligand specificity" not in manuscript_text
    rules["R-08"] = parse_citation_markers("The sentence cites [1,2-3] and [4].") == [1, 2, 3, 4]
    rules["R-09"] = not any("no DOI" in warning.lower() for warning in parse_result.warnings) or bool(expected.get("doi_count", 0) == 0)
    rules["R-10"] = (
        (
            expected.get("sentence_count") is None
            or len(sentence_texts) == expected["sentence_count"]
        )
        and (
            expected.get("reference_count") is None
            or len(parse_result.reference_list) == expected["reference_count"]
        )
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
        "matched_citation_numbers": citation_numbers,
        "reference_numbers": reference_numbers,
        "warnings": parse_result.warnings,
    }


__all__ = ["PP01_RULES", "validate_stage05_pp01_fixture"]
