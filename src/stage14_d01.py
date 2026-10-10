"""Stage 14 / D-01 judge-call ledger and paid-call guard validation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.judge_stage import build_call_log_entry, normalize_call_log

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage14_fixture.json"

STAGE14_RULES = [
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


def validate_stage14_pp01_fixture(
    call_log: list[dict[str, Any]] | None = None,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    resolved_log = call_log if call_log is not None else fixture.get("call_log", [])
    normalized = normalize_call_log(resolved_log)
    paid_calls = sum(1 for row in normalized if row.get("paid") is True)
    valid_entries = sum(1 for row in normalized if row.get("raw_reply") and row["raw_reply"].strip())

    rules: dict[str, bool] = {}
    rules["R-01"] = isinstance(normalized, list)
    rules["R-02"] = all("call_key" in row for row in normalized)
    rules["R-03"] = all("raw_reply" in row for row in normalized)
    rules["R-04"] = all(isinstance(row.get("prompt_tokens"), int) for row in normalized)
    rules["R-05"] = all(isinstance(row.get("completion_tokens"), int) for row in normalized)
    rules["R-06"] = all(isinstance(row.get("cost_usd"), (int, float)) for row in normalized)
    rules["R-07"] = all(row.get("model") in {"gpt-5.6-luna", "gpt-5.6-sol"} for row in normalized)
    rules["R-08"] = all(row.get("stage") in {"judge_stage", "extraction_stage"} for row in normalized)
    rules["R-09"] = paid_calls == 0
    rules["R-10"] = valid_entries >= 1 or not normalized

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id"),
        "call_log": normalized,
        "paid_calls": paid_calls,
        "valid_entries": valid_entries,
    }


__all__ = ["STAGE14_RULES", "validate_stage14_pp01_fixture"]
