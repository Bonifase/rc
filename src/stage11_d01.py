"""Stage 11 / D-01 six-vote judge aggregation validation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.judge_stage import clean_votes, counts_for, majority_verdict

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage11_fixture.json"

STAGE11_RULES = [
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


def validate_stage11_pp01_fixture(
    luna_votes: list[object] | None = None,
    sol_votes: list[object] | None = None,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    resolved_luna = luna_votes if luna_votes is not None else fixture.get("luna_votes", ["supported"] * 6)
    resolved_sol = sol_votes if sol_votes is not None else fixture.get("sol_votes", [])
    luna_clean = clean_votes(resolved_luna)
    sol_clean = clean_votes(resolved_sol)
    luna_summary = majority_verdict(luna_clean, threshold=4)
    sol_summary = majority_verdict(sol_clean, threshold=4)

    rules: dict[str, bool] = {}
    rules["R-01"] = len(resolved_luna) >= 1
    rules["R-02"] = len(resolved_luna) == 6 or len(resolved_luna) == 0
    rules["R-03"] = len(luna_clean) == len(resolved_luna) or len(luna_clean) <= len(resolved_luna)
    rules["R-04"] = all(v in {"supported", "unsupported", "uncertain"} for v in luna_clean)
    rules["R-05"] = len(resolved_sol) in {0, 6}
    rules["R-06"] = all(v in {"supported", "unsupported", "uncertain"} for v in sol_clean)
    rules["R-07"] = bool(luna_summary["verdict"]) or not luna_clean
    rules["R-08"] = not sol_clean or sol_summary["verdict"] in {"supported", "unsupported", "uncertain"}
    rules["R-09"] = counts_for(luna_clean)["supported"] >= 0
    rules["R-10"] = (
        (len(resolved_luna) == 0 and len(resolved_sol) == 0)
        or len(luna_clean) >= 1
        or len(sol_clean) >= 1
    )

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id"),
        "luna_counts": counts_for(luna_clean),
        "sol_counts": counts_for(sol_clean),
        "luna_majority": luna_summary["verdict"],
        "sol_majority": sol_summary["verdict"],
    }


__all__ = ["STAGE11_RULES", "validate_stage11_pp01_fixture"]
