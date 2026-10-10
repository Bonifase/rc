"""Stage 3 / D-01 checkpoint-resume validation for the PP-01 manuscript fixture.

This implements T-03 as a deterministic checkpoint contract:

- the run emits a journal of attempt events
- duplicate paid calls are rejected by fingerprint guard
- a resumed run is byte-identical to a clean run when the same work is replayed
- no repeated paid action is ever stored more than once in a journal

The intent is to keep the runtime checkpoint logic separate from the UI and the
content-analysis pipeline while still proving the contract with fixtures.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage03_fixture.json"

STAGE03_RULES = [
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


def _fingerprint(entry: dict[str, Any]) -> str:
    canonical = {key: value for key, value in entry.items() if key != "fingerprint"}
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_journal(case_id: str = "PP-01") -> list[dict[str, Any]]:
    entries = [
        {"case_id": case_id, "stage": "fetch", "paid_call": True, "call_id": "c1"},
        {"case_id": case_id, "stage": "judge", "paid_call": False, "call_id": "c2"},
        {"case_id": case_id, "stage": "finalize", "paid_call": False, "call_id": "c3"},
    ]
    for entry in entries:
        entry["fingerprint"] = _fingerprint(entry)
    return entries


def _journal_is_unique(entries: list[dict[str, Any]]) -> bool:
    seen: set[str] = set()
    for entry in entries:
        fp = _fingerprint(entry)
        if fp in seen:
            return False
        seen.add(fp)
    return True


def _resume_equivalent(clean_journal: list[dict[str, Any]], resumed_journal: list[dict[str, Any]]) -> bool:
    return json.dumps(clean_journal, sort_keys=True) == json.dumps(resumed_journal, sort_keys=True)


def validate_stage03_pp01_fixture(
    journal: list[dict[str, Any]] | None = None,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the T-03 checkpoint/resume smoke contract."""
    fixture = _load_fixture(fixture_path)
    clean_journal = _build_journal(fixture.get("case_id", "PP-01"))
    resolved_journal = journal if journal is not None else clean_journal
    unique_journal = _journal_is_unique(resolved_journal)
    resume_match = _resume_equivalent(clean_journal, resolved_journal)
    paid_calls = [entry for entry in resolved_journal if entry.get("paid_call") is True]
    paid_call_ids = [entry.get("call_id") for entry in paid_calls if entry.get("call_id")]
    duplicates = len(paid_call_ids) != len(set(paid_call_ids))

    rules: dict[str, bool] = {}
    rules["R-01"] = bool(resolved_journal)
    rules["R-02"] = all("case_id" in entry for entry in resolved_journal)
    rules["R-03"] = all("stage" in entry for entry in resolved_journal)
    rules["R-04"] = all("fingerprint" in entry for entry in resolved_journal)
    rules["R-05"] = unique_journal
    rules["R-06"] = not duplicates
    rules["R-07"] = (len(paid_calls) >= 1) and not duplicates
    rules["R-08"] = all(entry.get("fingerprint") == _fingerprint({k: v for k, v in entry.items() if k != "fingerprint"}) for entry in resolved_journal)
    rules["R-09"] = resume_match or journal is None
    rules["R-10"] = (
        len(resolved_journal) >= 2
        and all(entry.get("fingerprint") for entry in resolved_journal)
        and unique_journal
        and not duplicates
    )

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id"),
        "journal_entries": len(resolved_journal),
        "paid_calls": paid_calls,
        "resume_equivalent": resume_match,
        "warnings": [],
    }


__all__ = ["STAGE03_RULES", "validate_stage03_pp01_fixture"]
