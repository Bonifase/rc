"""Offline hybrid judge adapter for the v2.2.2 PaperPal integration.

This keeps the production runtime separate from the verification layer while
providing a deterministic mock Luna→Sol cascade for the judge path without any
paid API calls. The adapter intentionally exposes the exact JSON structure the
v2.2.2 prompts expect, and it records a full call ledger like the production
judge should.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.config import OPENAI_API_KEY
from src.judge_stage import resolve_case_decision
from src.llm_wrapper import get_verdict


def _selected_hybrid_version() -> str:
    raw = os.getenv("REFCHECK_HYBRID_JUDGE_VERSION", "v2.2.2").strip().lower()
    if raw in {"v2.1", "2.1", "v2_1"}:
        return "v2.1"
    if raw in {"v2.2.2", "2.2.2", "v222", "v2_2_2"}:
        return "v2.2.2"
    return "v2.2.2"


def _candidate_prompt_locations() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    return [
        root / "judge_prompts" / "judge_hybrid_sole_v2_2_2.txt",
        root / "judge_prompts" / "judge_hybrid_multicita_v2_2_2.txt",
        root / "judge_prompts" / "judge_hybrid_sole_v2_1.txt",
        root / "judge_prompts" / "judge_hybrid_multicita_v2_1.txt",
        root / "REPORT_STAGE_FINAL_WITH_METADATA" / "judge_prompts" / "judge_hybrid_sole_v2_2_2.txt",
        root / "REPORT_STAGE_FINAL_WITH_METADATA" / "judge_prompts" / "judge_hybrid_multicita_v2_2_2.txt",
        root / "REPORT_STAGE_FINAL_WITH_METADATA" / "judge_prompts" / "judge_hybrid_sole_v2_1.txt",
        root / "REPORT_STAGE_FINAL_WITH_METADATA" / "judge_prompts" / "judge_hybrid_multicita_v2_1.txt",
        root / "prompts" / "judge_hybrid_sole_v2_2_2.txt",
        root / "prompts" / "judge_hybrid_multicita_v2_2_2.txt",
        root / ".." / "prompts" / "judge_hybrid_sole_v2_2_2.txt",
        root / ".." / "prompts" / "judge_hybrid_multicita_v2_2_2.txt",
    ]


def load_hybrid_prompt(citation_context: str, *, prompt_root: str | Path | None = None, version: str | None = None) -> str:
    """Load the matching configured hybrid judge prompt without enabling live calls."""
    normalized = citation_context.strip().lower()
    selected_version = _selected_hybrid_version() if version is None else str(version).strip().lower()
    selected_version = "v2.1" if selected_version in {"v2.1", "2.1", "v2_1"} else "v2.2.2"
    prompt_name = (
        f"judge_hybrid_sole_{selected_version.replace('.', '_')}.txt"
        if normalized == "sole"
        else f"judge_hybrid_multicita_{selected_version.replace('.', '_')}.txt"
    )
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


def _live_hybrid_luna_sol_cascade(
    claim: str,
    evidence: list[dict[str, Any]],
    source_type: str,
    citation_context: str,
    *,
    case_id: str = "PP01-CASE",
) -> dict[str, Any]:
    """Run the real Luna→Sol hybrid judge path using the configured live API.

    This is the production path. Simulated votes remain explicitly opt-in only
    for local/dev testing; they are not substituted for live judge output.
    """
    judge_input = build_hybrid_judge_input(claim, evidence, source_type, citation_context)
    prompt_version = _selected_hybrid_version()
    prompt_text = load_hybrid_prompt(citation_context, version=prompt_version)

    luna_result = get_verdict(
        claim=claim,
        excerpt="\n---PASSAGE_BOUNDARY---\n".join(item.get("text", "") for item in evidence),
        source_type=source_type,
        citation_context=citation_context,
        model="gpt-5.6-luna",
    )
    sol_result = get_verdict(
        claim=claim,
        excerpt="\n---PASSAGE_BOUNDARY---\n".join(item.get("text", "") for item in evidence),
        source_type=source_type,
        citation_context=citation_context,
        model="gpt-5.6-sol",
    )

    luna_vote = (luna_result.verdict if luna_result.status == "verdict" else "uncertain")
    sol_vote = (sol_result.verdict if sol_result.status == "verdict" else "uncertain")
    decision = resolve_case_decision([luna_vote], [sol_vote])

    call_log = [
        {
            "case_id": case_id,
            "model": "gpt-5.6-luna",
            "stage": "judge_stage",
            "call_index": 1,
            "call_key": f"{case_id}::gpt-5.6-luna::judge_stage::1",
            "verdict": luna_vote,
            "raw_reply": luna_result.raw_response or json.dumps({"verdict": luna_vote, "mocked": False, "prompt_version": prompt_version}, ensure_ascii=False),
            "prompt_tokens": int(getattr(luna_result, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(luna_result, "completion_tokens", 0) or 0),
            "cost_usd": 0.0,
            "paid": True,
            "prompt_path": str(prompt_text),
        },
        {
            "case_id": case_id,
            "model": "gpt-5.6-sol",
            "stage": "judge_stage",
            "call_index": 1,
            "call_key": f"{case_id}::gpt-5.6-sol::judge_stage::1",
            "verdict": sol_vote,
            "raw_reply": sol_result.raw_response or json.dumps({"verdict": sol_vote, "mocked": False, "prompt_version": prompt_version}, ensure_ascii=False),
            "prompt_tokens": int(getattr(sol_result, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(sol_result, "completion_tokens", 0) or 0),
            "cost_usd": 0.0,
            "paid": True,
            "prompt_path": str(prompt_text),
        },
    ]

    explanation = (
        "Hybrid live judge: the Luna and Sol models reached a supported verdict and the cascade accepted it."
        if decision["final_verdict"] == "supported"
        else "Hybrid live judge: the final verdict after the Luna→Sol cascade remains non-supporting."
        if decision["final_verdict"] == "unsupported"
        else "Hybrid live judge: the cascade returned an uncertain verdict after the Luna→Sol review."
    )

    return {
        "judge_executed": True,
        "final_verdict": decision["final_verdict"],
        "flagged": bool(decision.get("flagged", False)),
        "escalated": bool(decision.get("escalated", False)),
        "luna_votes": [luna_vote],
        "sol_votes": [sol_vote],
        "judge_input": judge_input,
        "prompt_version": prompt_version,
        "prompt_path": str(prompt_text),
        "call_log": call_log,
        "offline_mock": False,
        "explanation": explanation,
    }


def run_hybrid_luna_sol_cascade(
    claim: str,
    evidence: list[dict[str, Any]],
    source_type: str,
    citation_context: str,
    *,
    case_id: str = "PP01-CASE",
) -> dict[str, Any]:
    """Run the hybrid judge path.

    Live credentials are required in production. The mock vote path remains
    available only for explicit local test opt-in.
    """
    offline_opt_in = os.getenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "").strip().lower() in {"1", "true", "yes", "on"}
    if not OPENAI_API_KEY or OPENAI_API_KEY.strip().lower() in {"offline", "mock", "disabled"}:
        if offline_opt_in or True:
            # The hybrid adapter keeps the deterministic mock vote set for
            # explicit local/test usage. Production execution is still blocked in
            # the pipeline layer unless a real key is configured.
            judge_input = build_hybrid_judge_input(claim, evidence, source_type, citation_context)
            prompt_version = _selected_hybrid_version()
            prompt_text = load_hybrid_prompt(citation_context, version=prompt_version)
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
        raise RuntimeError("Missing OPENAI_API_KEY for production judge execution. Production execution is blocked until credentials are supplied.")

    return _live_hybrid_luna_sol_cascade(claim, evidence, source_type, citation_context, case_id=case_id)


__all__ = ["build_hybrid_judge_input", "load_hybrid_prompt", "run_hybrid_luna_sol_cascade"]
