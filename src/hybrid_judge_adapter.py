"""Offline hybrid judge adapter for the v2.2.2 PaperPal integration.

This keeps the production runtime separate from the verification layer while
providing a deterministic mock Luna→Sol cascade for the judge path without any
paid API calls. The adapter intentionally exposes the exact JSON structure the
v2.2.2 prompts expect, and it records a full call ledger like the production
judge should.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.judge_stage import resolve_case_decision


def _candidate_prompt_locations() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    return [
        root / "prompts" / "judge_hybrid_sole_v2_2_2.txt",
        root / "prompts" / "judge_hybrid_multicita_v2_2_2.txt",
        root / ".." / "prompts" / "judge_hybrid_sole_v2_2_2.txt",
        root / ".." / "prompts" / "judge_hybrid_multicita_v2_2_2.txt",
    ]


def load_hybrid_prompt(citation_context: str, *, prompt_root: str | Path | None = None) -> str:
    """Load the matching v2.2.2 prompt if it is present in the environment."""
    normalized = citation_context.strip().lower()
    prompt_name = "judge_hybrid_sole_v2_2_2.txt" if normalized == "sole" else "judge_hybrid_multicita_v2_2_2.txt"
    search_roots: list[Path] = []
    if prompt_root is not None:
        search_roots.append(Path(prompt_root))
    search_roots.extend(_candidate_prompt_locations())

    for base in search_roots:
        candidate = base if base.name == prompt_name else base / prompt_name
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return prompt_name


def build_hybrid_judge_input(
    claim: str,
    evidence: list[dict[str, Any]],
    source_type: str,
    citation_context: str,
) -> dict[str, Any]:
    """Construct the exact JSON object required by the hybrid judge prompt."""
    return {
        "citation_context": citation_context,
        "claim": claim,
        "evidence": evidence,
        "source_type": source_type,
    }


def _mock_vote(claim: str, evidence: list[dict[str, Any]]) -> str:
    claim_text = (claim or "").lower()
    evidence_text = "\n".join(str(item.get("text", "")) for item in evidence).lower()

    if not claim_text or not evidence_text:
        return "uncertain"

    positive_markers = [
        "reduced", "increased", "improved", "enhanced", "decreased",
        "supports", "supports", "associated with", "led to", "improves"
    ]
    contradiction_markers = [
        "did not", "no effect", "not observed", "no difference", "opposite",
        "instead of", "did not differ", "not significantly", "decreased instead"
    ]

    has_positive = any(marker in claim_text or marker in evidence_text for marker in positive_markers)
    has_contradiction = any(marker in evidence_text for marker in contradiction_markers)
    if has_contradiction and has_positive:
        return "unsupported"
    if has_positive and not has_contradiction:
        return "supported"
    if has_contradiction:
        return "unsupported"
    return "uncertain"


def run_hybrid_luna_sol_cascade(
    claim: str,
    evidence: list[dict[str, Any]],
    source_type: str,
    citation_context: str,
    *,
    case_id: str = "PP01-CASE",
) -> dict[str, Any]:
    """Run an offline mocked Luna→Sol judge cascade and return a machine-readable result."""
    judge_input = build_hybrid_judge_input(claim, evidence, source_type, citation_context)
    prompt_version = "judge_hybrid_v2_2_2"
    prompt_text = load_hybrid_prompt(citation_context)

    luna_votes = [_mock_vote(claim, evidence) for _ in range(6)]
    sol_votes: list[str] = []

    if luna_votes and all(vote == "supported" for vote in luna_votes):
        final_verdict = "supported"
        flagged = False
        escalated = False
    else:
        sol_votes = [_mock_vote(claim, evidence) for _ in range(6)]
        decision = resolve_case_decision(luna_votes, sol_votes)
        final_verdict = decision["final_verdict"]
        flagged = decision["flagged"]
        escalated = decision["escalated"]

    call_log = []
    for idx, vote in enumerate(luna_votes, start=1):
        call_log.append({
            "case_id": case_id,
            "model": "gpt-5.6-luna",
            "stage": "judge_stage",
            "call_index": idx,
            "call_key": f"{case_id}::gpt-5.6-luna::judge_stage::{idx}",
            "verdict": vote,
            "raw_reply": json.dumps({"verdict": vote, "mocked": True, "prompt_version": prompt_version}, ensure_ascii=False),
            "prompt_tokens": 2048,
            "completion_tokens": 256,
            "cost_usd": 0.0,
            "paid": False,
            "prompt_path": str(prompt_text),
        })

    for idx, vote in enumerate(sol_votes, start=1):
        call_log.append({
            "case_id": case_id,
            "model": "gpt-5.6-sol",
            "stage": "judge_stage",
            "call_index": idx,
            "call_key": f"{case_id}::gpt-5.6-sol::judge_stage::{idx}",
            "verdict": vote,
            "raw_reply": json.dumps({"verdict": vote, "mocked": True, "prompt_version": prompt_version}, ensure_ascii=False),
            "prompt_tokens": 4096,
            "completion_tokens": 512,
            "cost_usd": 0.0,
            "paid": False,
            "prompt_path": str(prompt_text),
        })

    explanation = (
        "Offline mocked hybrid judge: the evidence supports the claim in the supplied excerpt, so the matched verdict is returned without any paid API call."
        if final_verdict == "supported"
        else "Offline mocked hybrid judge: the evidence is not consistent with the claim or is insufficiently specific, so the cascade returns the non-supporting verdict."
        if final_verdict == "unsupported"
        else "Offline mocked hybrid judge: the evidence is mixed or insufficiently specific, so the result is uncertain."
    )

    return {
        "judge_executed": True,
        "final_verdict": final_verdict,
        "flagged": flagged,
        "escalated": escalated,
        "luna_votes": luna_votes,
        "sol_votes": sol_votes,
        "judge_input": judge_input,
        "prompt_version": prompt_version,
        "prompt_path": str(prompt_text),
        "call_log": call_log,
        "offline_mock": True,
        "explanation": explanation,
    }


__all__ = ["build_hybrid_judge_input", "load_hybrid_prompt", "run_hybrid_luna_sol_cascade"]
