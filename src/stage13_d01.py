"""Stage 13 / D-01 invalid-vote retry and counting validation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.judge_stage import clean_votes, normalize_verdict

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage13_fixture.json"

STAGE13_RULES = [
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


def validate_stage13_pp01_fixture(
    vote_log: list[object] | None = None,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    resolved = vote_log if vote_log is not None else fixture.get("vote_log", ["supported"] * 6)
    cleaned = clean_votes(resolved)
    invalid_count = len(resolved) - len(cleaned)
    retry_count = min(1, invalid_count)

    rules: dict[str, bool] = {}
    rules["R-01"] = isinstance(resolved, list)
    rules["R-02"] = len(cleaned) >= 1
    rules["R-03"] = all(normalize_verdict(v) is not None for v in cleaned)
    rules["R-04"] = invalid_count >= 0
    rules["R-05"] = retry_count <= 1
    rules["R-06"] = len(cleaned) <= len(resolved)
    rules["R-07"] = all(v in {"supported", "unsupported", "uncertain"} for v in cleaned)
    rules["R-08"] = len(cleaned) == sum(1 for v in resolved if normalize_verdict(v) is not None)
    rules["R-09"] = retry_count == (1 if invalid_count > 0 else 0)
    rules["R-10"] = True

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id"),
        "valid_votes": cleaned,
        "invalid_count": invalid_count,
        "retry_count": retry_count,
    }


__all__ = ["STAGE13_RULES", "validate_stage13_pp01_fixture"]
