"""Stage 12 / D-01 Luna→Sol cascade and flag validation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.judge_stage import clean_votes, counts_for, resolve_case_decision

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage12_fixture.json"

STAGE12_RULES = [
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


def validate_stage12_pp01_fixture(
    luna_votes: list[object] | None = None,
    sol_votes: list[object] | None = None,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    resolved_luna = luna_votes if luna_votes is not None else fixture.get("luna_votes", ["unsupported"] * 6)
    resolved_sol = sol_votes if sol_votes is not None else fixture.get("sol_votes", ["unsupported"] * 6)
    decision = resolve_case_decision(resolved_luna, resolved_sol)
    luna_clean = clean_votes(resolved_luna)
    sol_clean = clean_votes(resolved_sol)

    rules: dict[str, bool] = {}
    rules["R-01"] = len(luna_clean) >= 1
    rules["R-02"] = len(resolved_luna) in {0, 6}
    rules["R-03"] = len(resolved_sol) in {0, 6}
    rules["R-04"] = all(v in {"supported", "unsupported", "uncertain"} for v in luna_clean)
    rules["R-05"] = all(v in {"supported", "unsupported", "uncertain"} for v in sol_clean)
    rules["R-06"] = decision["final_verdict"] in {"supported", "unsupported", "uncertain"}
    rules["R-07"] = isinstance(decision["flagged"], bool)
    rules["R-08"] = decision["escalated"] in {True, False}
    rules["R-09"] = decision["luna_counts"] == counts_for(luna_clean)
    rules["R-10"] = decision["sol_counts"] == counts_for(sol_clean)

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id"),
        "decision": decision,
        "luna_votes": list(resolved_luna),
        "sol_votes": list(resolved_sol),
    }


__all__ = ["STAGE12_RULES", "validate_stage12_pp01_fixture"]
