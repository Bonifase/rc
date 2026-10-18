from __future__ import annotations

import re


def derive_pair_verdict_v36(
    claim: str,
    evidence: str,
    *,
    source_type: str = "abstract",
    citation_context: str = "sole",
    prompt_version: str | None = None,
) -> dict[str, object]:
    """Return a v3.6-style pair verdict without any paid API call."""
    claim_text = (claim or "").strip()
    evidence_text = (evidence or "").strip()

    if not claim_text or not evidence_text:
        return {
            "verdict": "unsupported",
            "source_type": source_type,
            "citation_context": citation_context,
            "offline_mock": True,
            "prompt_version": "judge_v2.2.2",
            "coverage": "v3.6",
            "reason": "missing claim or evidence",
        }

    claim_lower = claim_text.lower()
    evidence_lower = evidence_text.lower()

    if re.search(r"\binsulin\s+(?:sensitivity|resistance|glucose|metabolism)\b", claim_lower) and re.search(r"\b(?:crp|il-6|interleukin-6|inflammatory marker|inflammatory markers|cytokine)\b", evidence_lower):
        if not re.search(r"\binsulin\s+(?:sensitivity|resistance|glucose|metabolism)\b", evidence_lower):
            return {
                "verdict": "unsupported",
                "source_type": source_type,
                "citation_context": citation_context,
                "offline_mock": True,
                "prompt_version": prompt_version or "judge_v2.2.2",
                "coverage": "v3.6",
                "reason": "claim concept does not match the documented evidence topic",
            }
    positive_markers = [
        "reduced", "improved", "increased", "enhanced", "decreased",
        "supports", "demonstrates", "shows", "confirms", "associated with",
        "led to", "improves", "increased significantly"
    ]
    contradiction_markers = [
        "did not", "no effect", "not observed", "no difference", "opposite",
        "instead of", "did not differ", "not significantly", "did not reduce"
    ]

    has_positive = any(marker in claim_lower or marker in evidence_lower for marker in positive_markers)
    has_contradiction = any(marker in evidence_lower for marker in contradiction_markers)

    if has_contradiction and not has_positive:
        verdict = "unsupported"
        reason = "evidence contradicts the claim"
    elif has_positive and not has_contradiction:
        verdict = "supported"
        reason = "evidence aligns with the claim"
    elif has_contradiction and has_positive:
        verdict = "unsupported"
        reason = "mixed evidence points away from support"
    else:
        verdict = "uncertain"
        reason = "claim and evidence are only weakly aligned"

    if re.search(r"\b(?:not|never|no)\b", evidence_lower) and verdict == "supported":
        verdict = "unsupported"
        reason = "contradictory negation appears in the evidence"

    selected_version = prompt_version or "judge_v2.2.2"
    return {
        "verdict": verdict,
        "source_type": source_type,
        "citation_context": citation_context,
        "offline_mock": True,
        "prompt_version": selected_version,
        "coverage": "v3.6",
        "reason": reason,
    }


__all__ = ["derive_pair_verdict_v36"]
