from __future__ import annotations

from typing import Any

from src.adapter_registry import apply_prompt_swap
from src.derive_pair_verdict_v36 import derive_pair_verdict_v36
from src.judge_stage import build_call_log_entry, resolve_case_decision


def judge_v30(
    claim: str,
    evidence: list[dict[str, Any]],
    source_type: str = "abstract",
    citation_context: str = "sole",
    *,
    case_id: str = "PP01-CASE",
) -> dict[str, Any]:
    """Run the deterministic offline judge v3.0 contract for the v3.6 adapter layer."""
    prompt_record = apply_prompt_swap("judge", "v2.2.2")
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
            ),
        }
        for item in evidence or []
    ]
    votes = [result["pair_verdict"]["verdict"] for result in pair_results] or ["supported"] * 6
    luna_votes = list(votes[:6]) if len(votes) >= 6 else list(votes) + [votes[0] if votes else "supported"] * (6 - len(votes))
    sol_votes = list(votes[:6]) if len(votes) >= 6 else list(votes) + [votes[0] if votes else "supported"] * (6 - len(votes))
    decision = resolve_case_decision(luna_votes, sol_votes)

    call_log = []
    for index, vote in enumerate(luna_votes, start=1):
        call_log.append(
            build_call_log_entry(
                case_id=case_id,
                model="gpt-5.6-luna",
                stage="judge_stage",
                call_index=index,
                verdict=vote,
                raw_reply=f"{{\"verdict\": \"{vote}\", \"offline_mock\": true, \"prompt_version\": \"{prompt_record['selected_version']}\"}}",
                prompt_tokens=2048,
                completion_tokens=256,
                cost_usd=0.0,
                paid=False,
            )
        )
    for index, vote in enumerate(sol_votes, start=1):
        call_log.append(
            build_call_log_entry(
                case_id=case_id,
                model="gpt-5.6-sol",
                stage="judge_stage",
                call_index=index,
                verdict=vote,
                raw_reply=f"{{\"verdict\": \"{vote}\", \"offline_mock\": true, \"prompt_version\": \"{prompt_record['selected_version']}\"}}",
                prompt_tokens=4096,
                completion_tokens=512,
                cost_usd=0.0,
                paid=False,
            )
        )

    return {
        "judge_executed": True,
        "final_verdict": decision["final_verdict"],
        "flagged": decision["flagged"],
        "escalated": decision["escalated"],
        "luna_votes": list(luna_votes),
        "sol_votes": list(sol_votes),
        "source_type": source_type,
        "citation_context": citation_context,
        "offline_mock": True,
        "prompt_version": prompt_record["selected_version"],
        "prompt_hash": prompt_record["prompt_hash"],
        "pair_results": pair_results,
        "call_log": call_log,
    }


__all__ = ["judge_v30"]
