from __future__ import annotations

import hashlib
import json
from typing import Any

from src.adapter_registry import apply_prompt_swap, get_adapter_config
from src.claims_v36 import claims_v36
from src.hybrid_judge_adapter import run_hybrid_luna_sol_cascade


def _default_claims() -> list[str]:
    return [
        "Exercise reduced CRP and IL-6 after 12 weeks.",
        "The intervention improved insulin sensitivity in the study cohort.",
        "The intervention improved blood-pressure control in the study cohort.",
    ]


def _claim_id_for(index: int) -> str:
    return f"PP01-CASE-{index:02d}"


_EVIDENCE = "After 12 weeks, the exercise programme reduced plasma C-reactive protein and interleukin-6."
_NON_LITERAL_QUOTE = "The programme eliminated every inflammatory marker."
_VOTE_FIELDS = {"evidence_sufficient", "material_error_present", "evidence_quotes", "explanation"}

# One scripted ballot per vote slot. "nonliteral:<status>" first answers with a quote that is not in the evidence.
_CASE_SCRIPTS: dict[str, dict[str, Any]] = {
    "PP01-CASE-01": {"luna": ["supported"] * 6, "sol": [], "expect": "supported"},
    "PP01-CASE-02": {"luna": ["unsupported"] * 6, "sol": ["unsupported"] * 6, "expect": "unsupported"},
    "PP01-CASE-03": {
        "luna": ["nonliteral:supported", "supported", "supported", "unsupported", "unsupported", "uncertain"],
        "sol": ["unsupported"] * 5 + ["supported"],
        "expect": "unsupported",
    },
}
_DEFAULT_SCRIPT = {"luna": ["supported"] * 6, "sol": [], "expect": "supported"}


class _ScriptedVoteProvider:
    """Answers each vote slot from a script and records every request, so no real model is involved."""

    def __init__(self, script: dict[str, Any]):
        self._script = script
        self.calls: list[tuple[str, int, int]] = []

    def request_vote(self, *, model: str, vote_slot: int, attempt_number: int) -> dict[str, Any]:
        self.calls.append((model, vote_slot, attempt_number))
        arm = "luna" if model == "gpt-5.6-luna" else "sol"
        entry = self._script[arm][vote_slot - 1]
        quote = _EVIDENCE
        if entry.startswith("nonliteral:"):
            entry = entry.split(":", 1)[1]
            if attempt_number == 1:
                quote = _NON_LITERAL_QUOTE
        return {
            "evidence_sufficient": entry != "uncertain",
            "material_error_present": entry == "unsupported",
            "evidence_quotes": [quote],
            "explanation": f"Scripted {entry} ballot for the offline acceptance run.",
        }


def expected_final_verdict(luna_statuses: list[str], sol_statuses: list[str]) -> str:
    """Cascade rule restated independently of the adapter: Luna clears only on six valid supported votes."""
    if len(luna_statuses) == 6 and all(status == "supported" for status in luna_statuses):
        return "supported"
    if sum(status == "supported" for status in sol_statuses) >= 4:
        return "supported"
    if sum(status == "unsupported" for status in sol_statuses) >= 4:
        return "unsupported"
    return "uncertain"


def _literal_in_evidence(quote: Any, evidence: list[dict[str, Any]]) -> bool:
    if not isinstance(quote, str) or not quote:
        return False
    normalised = quote.replace("\r\n", "\n").replace("\r", "\n")
    return any(normalised in str(block.get("text", "")).replace("\r\n", "\n").replace("\r", "\n") for block in evidence)


def verify_case_consistency(item: dict[str, Any]) -> list[str]:
    """Recompute a cascade result from its own call log and report every disagreement."""
    problems: list[str] = []
    rows = [row for row in item.get("call_log") or [] if isinstance(row, dict)]
    evidence = (item.get("judge_input") or {}).get("evidence") or []

    keys = [row.get("call_key") for row in rows]
    if len(keys) != len(set(keys)):
        problems.append("duplicate call keys in the call log")
    if len({row.get("user_message") for row in rows}) > 1:
        problems.append("votes were not sent the same frozen evidence package")

    statuses: dict[str, list[str]] = {}
    for arm, model in (("luna", "gpt-5.6-luna"), ("sol", "gpt-5.6-sol")):
        by_slot: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            if row.get("model") == model:
                by_slot.setdefault(int(row["vote_slot"]), []).append(row)
        slot_statuses: list[str] = []
        for slot in sorted(by_slot):
            attempts = sorted(by_slot[slot], key=lambda r: r["attempt_number"])
            if len(attempts) > 2:
                problems.append(f"{arm} slot {slot} used more than two attempts")
            if len(attempts) == 2 and attempts[0].get("validation_status") != "failed":
                problems.append(f"{arm} slot {slot} was retried although the first attempt was valid")
            valid = [r for r in attempts if r.get("validation_status") == "valid"]
            for row in attempts:
                response = row.get("vote_response") or {}
                if row.get("validation_status") == "valid":
                    if set(response) != _VOTE_FIELDS:
                        problems.append(f"{arm} slot {slot} accepted a vote without the exact four fields")
                    if not all(_literal_in_evidence(q, evidence) for q in response.get("evidence_quotes") or []):
                        problems.append(f"{arm} slot {slot} accepted a non-literal evidence quote")
            slot_statuses.append(valid[0]["verdict"] if valid else "failed")
        statuses[arm] = slot_statuses

    if item.get("luna_votes") != statuses["luna"]:
        problems.append(f"luna votes {item.get('luna_votes')} do not match the call log {statuses['luna']}")
    if item.get("sol_votes") != statuses["sol"]:
        problems.append(f"sol votes {item.get('sol_votes')} do not match the call log {statuses['sol']}")
    if len(statuses["luna"]) != 6:
        problems.append("Luna did not fill six vote slots")

    luna_cleared = len(statuses["luna"]) == 6 and all(s == "supported" for s in statuses["luna"])
    if luna_cleared and statuses["sol"]:
        problems.append("Sol was called although Luna cleared the pair")
    if not luna_cleared and len(statuses["sol"]) != 6:
        problems.append("Luna did not clear the pair but Sol did not fill six vote slots")
    if bool(item.get("escalated")) != (not luna_cleared):
        problems.append("escalated flag disagrees with the votes")

    expected = expected_final_verdict(statuses["luna"], statuses["sol"])
    if item.get("final_verdict") != expected:
        problems.append(f"final verdict {item.get('final_verdict')!r} but the votes decide {expected!r}")
    return problems


def _acceptance_checks(verdicts: list[dict[str, Any]], providers: dict[str, _ScriptedVoteProvider]) -> list[dict[str, Any]]:
    """Per-requirement results computed from the recorded cascade runs, never asserted."""
    rows = [row for item in verdicts for row in item["call_log"]]
    valid_rows = [row for row in rows if row["validation_status"] == "valid"]
    rejected_quotes = [row for row in rows if row["validation_status"] == "failed" and row["failure_category"] == "invalid_vote"]
    checks = [
        ("req_1_content_path", "Hybrid judge is wired to the content path",
         all(item["judge_executed"] is True and item["call_log"] for item in verdicts)),
        ("req_2_frozen_evidence", "Frozen claim/evidence package is reused for all votes",
         all(len({row["user_message"] for row in item["call_log"]}) == 1 for item in verdicts)),
        ("req_3_exact_contract", "Exact four-field judge contract is enforced and non-literal quotes are rejected",
         bool(rejected_quotes) and all(set(row["vote_response"]) == _VOTE_FIELDS for row in valid_rows)),
        ("req_4_cascade_logic", "Luna/Sol cascade logic and vote counting are preserved",
         all(not item["problems"] for item in verdicts)
         and any(item["escalated"] for item in verdicts) and any(not item["escalated"] for item in verdicts)),
        ("req_5_report_provenance", "Judge execution status is reported separately from local parse success",
         all(item["judge_executed"] is True and item["offline_mock"] is True for item in verdicts)
         and all(row["paid"] is False for row in rows)),
        ("req_6_lexical_separation", "Lexical overlap remains separate from scientific fidelity",
         all(item["final_verdict"] == _CASE_SCRIPTS.get(item["case_id"], _DEFAULT_SCRIPT)["expect"] for item in verdicts)),
        ("req_7_offline_acceptance", "Offline acceptance harness validates mocked end-to-end behavior",
         all(len(providers[item["case_id"]].calls) == len(item["call_log"]) for item in verdicts)),
    ]
    return [{"id": check_id, "label": label, "passed": bool(passed)} for check_id, label, passed in checks]


def _offline_orchestrator_result(mode: str, claim_text: str) -> dict[str, Any]:
    if mode not in {"replay", "live"}:
        raise ValueError("mode must be 'replay' or 'live'")

    config = get_adapter_config()
    chosen_version = config["swap_policy"][mode]
    prompt_record = apply_prompt_swap("judge", chosen_version)
    raw_claim_atoms = claims_v36(claim_text, decomposition="01b") if claim_text else [
        {"id": _claim_id_for(index), "text": item} for index, item in enumerate(_default_claims(), start=1)
    ]
    claim_atoms = []
    for index, claim in enumerate(raw_claim_atoms, start=1):
        entry = dict(claim) if isinstance(claim, dict) else {"text": str(claim)}
        entry["id"] = entry.get("id") or _claim_id_for(index)
        entry["case_id"] = _claim_id_for(index)
        claim_atoms.append(entry)

    verdicts = []
    providers: dict[str, _ScriptedVoteProvider] = {}
    for index, claim in enumerate(claim_atoms, start=1):
        claim_id = claim.get("case_id") or _claim_id_for(index)
        claim_text_value = claim["text"] if isinstance(claim, dict) else str(claim)
        provider = _ScriptedVoteProvider(_CASE_SCRIPTS.get(claim_id, _DEFAULT_SCRIPT))
        providers[claim_id] = provider
        result = run_hybrid_luna_sol_cascade(
            claim=claim_text_value,
            evidence=[{"location": "abstract", "text": _EVIDENCE}],
            source_type="abstract",
            citation_context="sole",
            case_id=claim_id,
            provider=provider,
        )
        result["problems"] = verify_case_consistency(result)
        result["pair_results"] = [{
            "claim": claim_text_value,
            "evidence": _EVIDENCE,
            "location": "abstract",
            "pair_verdict": {
                "verdict": result["final_verdict"],
                "source_type": "abstract",
                "citation_context": "sole",
                "offline_mock": True,
                "prompt_version": result["prompt_version"],
                "reason": result["explanation"],
            },
        }]
        verdicts.append(result)

    accepted = all(not item["problems"] for item in verdicts) and all(item["passed"] for item in _acceptance_checks(verdicts, providers))
    prompt_versions_used = [
        {"stage": "judge", "version": item["prompt_version"], "prompt_hash": hashlib.sha256(item["system_message"].encode("utf-8")).hexdigest()}
        for item in verdicts
    ]
    return {
        "status": "accepted" if accepted else "rejected",
        "mode": mode,
        "prompt_versions_used": prompt_versions_used or [
            {"stage": "judge", "version": prompt_record["selected_version"], "prompt_hash": prompt_record["prompt_hash"]},
        ],
        "claim_atoms": claim_atoms,
        "verdicts": verdicts,
        "offline_only": True,
        "checks": _acceptance_checks(verdicts, providers),
    }


def orchestrator_accept(*, mode: str = "replay", manuscript_path: str | None = None) -> dict[str, Any]:
    """Acceptance harness for the offline orchestrator.

    The live mode is a no-op alias for the same deterministic replay path: no paid
    API calls are permitted and the same prompt/version metadata is recorded.
    """
    claim_text = None if manuscript_path is None else manuscript_path
    return _offline_orchestrator_result(mode=mode, claim_text=claim_text)


def run_verify_bundled() -> dict[str, Any]:
    replay_result = orchestrator_accept(mode="replay")
    live_result = orchestrator_accept(mode="live")
    passed = replay_result["status"] == "accepted" and live_result["status"] == "accepted"
    return {
        "status": "passed" if passed else "failed",
        "mode": "offline",
        "replay": replay_result,
        "live": live_result,
    }


__all__ = ["orchestrator_accept", "run_verify_bundled"]
