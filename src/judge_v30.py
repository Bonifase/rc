from __future__ import annotations

import hashlib
import json
from typing import Any

from src.adapter_registry import apply_prompt_swap, get_adapter_config
from src.derive_pair_verdict_v36 import derive_pair_verdict_v36
from src.judge_stage import build_call_log_entry, resolve_case_decision


def _vote_response_for_verdict(verdict: str, evidence_text: str) -> dict[str, Any]:
    quote_text = evidence_text.strip()
    if verdict == "supported":
        return {
            "evidence_sufficient": True,
            "material_error_present": False,
            "evidence_quotes": [quote_text] if quote_text else [],
            "explanation": "The claim is supported by the supplied evidence and remains consistent with the source.",
        }
    if verdict == "unsupported":
        return {
            "evidence_sufficient": True,
            "material_error_present": True,
            "evidence_quotes": [quote_text] if quote_text else [],
            "explanation": "The source explicitly contradicts the claim's attribution, so the supplied evidence establishes a material error.",
        }
    return {
        "evidence_sufficient": False,
        "material_error_present": False,
        "evidence_quotes": [],
        "explanation": "The evidence is insufficient or mixed relative to the claim.",
    }


def _resolve_case_id(claim: str, case_id: str | None = None, *, index: int | None = None) -> str:
    if case_id and case_id != "PP01-CASE":
        return str(case_id)
    if index is not None:
        return f"PP01-CASE-{index:02d}"
    digest = hashlib.sha1((claim or "").strip().encode("utf-8")).hexdigest()[:8]
    return f"PP01-CASE-{digest}"


def judge_v30(
    claim: str,
    evidence: list[dict[str, Any]],
    source_type: str = "abstract",
    citation_context: str = "sole",
    *,
    case_id: str = "PP01-CASE",
    prompt_version: str | None = None,
) -> dict[str, Any]:
    """Run the deterministic offline judge v3.0 contract for the v3.6 adapter layer."""
    if prompt_version is None:
        config = get_adapter_config()
        default_version = config["swap_policy"].get("definitive", "judge_v2.2.2")
        prompt_version = default_version
    prompt_record = apply_prompt_swap("judge", prompt_version)
    resolved_case_id = _resolve_case_id(claim, case_id)
    pair_results = [
        {
            "claim": claim,
            "evidence": item.get("text", ""),
            "location": item.get("location", source_type),
            "pair_verdict": derive_pair_verdict_v36(
                claim,
                str(item.get("text", "")),
                source_type=source_type,
                citation_context=citation_context,
                prompt_version=prompt_record["selected_version"],
            ),
        }
        for item in evidence or []
    ]
    vote_verdicts = [result["pair_verdict"]["verdict"] for result in pair_results] or ["supported"] * 6
    luna_votes = list(vote_verdicts[:6]) if len(vote_verdicts) >= 6 else list(vote_verdicts) + [vote_verdicts[0] if vote_verdicts else "supported"] * (6 - len(vote_verdicts))

    call_log: list[dict[str, Any]] = []
    luna_statuses: list[str] = []
    for index, vote in enumerate(luna_votes, start=1):
        payload = _vote_response_for_verdict(vote, str((evidence or [{}])[0].get("text", "")) if evidence else "")
        raw_reply = json.dumps(payload, ensure_ascii=False)
        call_log.append(
            build_call_log_entry(
                case_id=resolved_case_id,
                model="gpt-5.6-luna",
                stage="judge_stage",
                call_index=index,
                verdict=vote,
                raw_reply=raw_reply,
                prompt_tokens=2048,
                completion_tokens=256,
                cost_usd=0.0,
                paid=False,
                evidence=evidence,
            )
        )
        luna_statuses.append(vote)

    if len(luna_statuses) == 6 and all(status == "supported" for status in luna_statuses):
        sol_votes = []
        sol_statuses = []
        decision = resolve_case_decision(luna_statuses, sol_votes)
        final_verdict = decision["final_verdict"]
        explanation = "The claim is supported by the supplied evidence and remains consistent with the source."
    else:
        sol_votes = []
        sol_statuses = []
        for index in range(1, 7):
            vote = luna_votes[index - 1] if index <= len(luna_votes) else "uncertain"
            if vote == "supported":
                sol_vote = "supported"
            elif vote == "unsupported":
                sol_vote = "unsupported"
            else:
                sol_vote = "uncertain"
            payload = _vote_response_for_verdict(sol_vote, str((evidence or [{}])[0].get("text", "")) if evidence else "")
            raw_reply = json.dumps(payload, ensure_ascii=False)
            call_log.append(
                build_call_log_entry(
                    case_id=resolved_case_id,
                    model="gpt-5.6-sol",
                    stage="judge_stage",
                    call_index=index,
                    verdict=sol_vote,
                    raw_reply=raw_reply,
                    prompt_tokens=4096,
                    completion_tokens=512,
                    cost_usd=0.0,
                    paid=False,
                    evidence=evidence,
                )
            )
            sol_statuses.append(sol_vote)
            sol_votes.append(sol_vote)
        decision = resolve_case_decision(luna_statuses, sol_statuses)
        final_verdict = decision["final_verdict"]
        explanation = (
            "The source supports the claim."
            if final_verdict == "supported"
            else "The source does not support the claim."
            if final_verdict == "unsupported"
            else "The evidence is insufficient or mixed relative to the claim."
        )

    return {
        "judge_executed": True,
        "final_verdict": final_verdict,
        "flagged": decision["flagged"],
        "escalated": decision["escalated"],
        "luna_votes": list(luna_statuses),
        "sol_votes": list(sol_statuses),
        "source_type": source_type,
        "citation_context": citation_context,
        "offline_mock": True,
        "prompt_version": prompt_record["selected_version"],
        "prompt_hash": prompt_record["prompt_hash"],
        "pair_results": pair_results,
        "call_log": call_log,
        "explanation": explanation,
        "case_id": resolved_case_id,
    }


__all__ = ["judge_v30"]
