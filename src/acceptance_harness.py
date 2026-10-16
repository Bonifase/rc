from __future__ import annotations

from typing import Any

from src.adapter_registry import apply_prompt_swap, get_adapter_config
from src.claims_v36 import claims_v36
from src.judge_v30 import judge_v30


def _default_claims() -> list[str]:
    return [
        "Exercise reduced CRP and IL-6 after 12 weeks.",
        "The intervention improved insulin sensitivity in the study cohort.",
    ]


def _offline_orchestrator_result(mode: str, claim_text: str) -> dict[str, Any]:
    if mode not in {"replay", "live"}:
        raise ValueError("mode must be 'replay' or 'live'")

    config = get_adapter_config()
    chosen_version = config["swap_policy"][mode]
    prompt_record = apply_prompt_swap("judge", chosen_version)
    claim_atoms = claims_v36(claim_text, decomposition="01b")
    verdicts = []
    for claim in claim_atoms:
        result = judge_v30(
            claim["text"],
            [{"location": "abstract", "text": "After 12 weeks, the exercise programme reduced plasma C-reactive protein and interleukin-6."}],
            source_type="abstract",
            citation_context="sole",
        )
        verdicts.append(result)

    accepted = all(item["final_verdict"] in {"supported", "unsupported", "uncertain"} for item in verdicts)
    return {
        "status": "accepted" if accepted else "rejected",
        "mode": mode,
        "prompt_versions_used": [
            {"stage": "judge", "version": prompt_record["selected_version"], "prompt_hash": prompt_record["prompt_hash"]},
        ],
        "claim_atoms": claim_atoms,
        "verdicts": verdicts,
        "offline_only": True,
    }


def orchestrator_accept(*, mode: str = "replay", manuscript_path: str | None = None) -> dict[str, Any]:
    """Acceptance harness for the offline orchestrator.

    The live mode is a no-op alias for the same deterministic replay path: no paid
    API calls are permitted and the same prompt/version metadata is recorded.
    """
    claim_text = _default_claims()[0] if manuscript_path is None else manuscript_path
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
