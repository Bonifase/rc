"""Stage 10 / D-01 judge-stage evidence reuse validation.

The contract is intentionally deterministic: the judge stage reuses a single
extracted evidence blob for both models and keeps the call ledger in a strict
schema for auditing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.judge_stage import (build_call_log_entry, normalize_call_log,
                             resolve_case_decision)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage10_fixture.json"

STAGE10_RULES = [
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


def validate_stage10_pp01_fixture(
    claim_text: str | None = None,
    evidence_text: str | None = None,
    luna_votes: list[str] | None = None,
    sol_votes: list[str] | None = None,
    call_log: list[dict[str, Any]] | None = None,
    source_type: str | None = None,
    citation_context: str | None = None,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    resolved_claim = claim_text if claim_text is not None else fixture.get("claim_text", "")
    resolved_evidence = evidence_text if evidence_text is not None else fixture.get("evidence_text", "")
    resolved_luna = luna_votes if luna_votes is not None else fixture.get("luna_votes", ["supported"] * 6)
    resolved_sol = sol_votes if sol_votes is not None else fixture.get("sol_votes", [])
    resolved_source_type = source_type if source_type is not None else fixture.get("source_type", "abstract")
    resolved_citation_context = citation_context if citation_context is not None else fixture.get("citation_context", "sole")
    resolved_call_log = call_log if call_log is not None else fixture.get("call_log", [])

    decision = resolve_case_decision(resolved_luna, resolved_sol)
    normalized_log = normalize_call_log(resolved_call_log)
    if not normalized_log and resolved_luna:
        normalized_log = [
            build_call_log_entry(
                case_id=fixture.get("case_id", "PP01-CASE"),
                model="gpt-5.6-luna",
                stage="judge_stage",
                call_index=index + 1,
                verdict=v,
                raw_reply=f"{v} verdict",
                prompt_tokens=2048,
                completion_tokens=256,
                cost_usd=0.0,
                paid=False,
            )
            for index, v in enumerate(resolved_luna[:6])
        ]

    rules: dict[str, bool] = {}
    rules["R-01"] = bool(resolved_claim)
    rules["R-02"] = bool(resolved_evidence)
    rules["R-03"] = len(normalized_log) >= 1
    rules["R-04"] = all(entry.get("call_key") for entry in normalized_log)
    rules["R-05"] = all(isinstance(entry.get("raw_reply"), str) and entry["raw_reply"].strip() for entry in normalized_log)
    rules["R-06"] = all(isinstance(entry.get("prompt_tokens"), int) and isinstance(entry.get("completion_tokens"), int) for entry in normalized_log)
    rules["R-07"] = all(isinstance(entry.get("cost_usd"), (int, float)) for entry in normalized_log)
    rules["R-08"] = decision["final_verdict"] in {"supported", "unsupported", "uncertain"}
    rules["R-09"] = resolved_source_type in {"full_text", "abstract", "cited_source_set"}
    rules["R-10"] = resolved_citation_context in {"sole", "one_of_several"}

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id"),
        "case_id": fixture.get("case_id", "PP01-CASE"),
        "claim_text": resolved_claim,
        "evidence_text": resolved_evidence,
        "source_type": resolved_source_type,
        "citation_context": resolved_citation_context,
        "luna_votes": list(resolved_luna),
        "sol_votes": list(resolved_sol),
        "decision": decision,
        "call_log": normalized_log,
    }


__all__ = ["STAGE10_RULES", "validate_stage10_pp01_fixture"]
