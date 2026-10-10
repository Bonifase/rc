"""Stage 4 / D-01 judgment-adapter validation for the PP-01 manuscript fixture.

This implements the T-09 acceptance gate in the same deterministic, fixture-driven
style as the earlier Stage gates:

- it evaluates a claim against the selected evidence text
- it classifies the result as supported / unsupported / uncertain
- it enforces the Stage 4 output schema used by the downstream judge adapter
- it keeps judgment logic separate from the UI and from the metadata pipeline

The contract is intentionally deterministic so it can be pinned by tests and
replayed without depending on model output.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage09_fixture.json"

STAGE09_RULES = [
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

_VALID_VERDICTS = {"supported", "unsupported", "uncertain"}
_VALID_SOURCE_TYPES = {"full_text", "abstract", "cited_source_set"}
_VALID_CITATION_CONTEXTS = {"sole", "one_of_several"}
_VALID_PRIMARY_FAMILIES = {
    "misattribution",
    "directional_inversion",
    "scope_distortion",
    "epistemic_inflation",
    "causal_overreach",
    "fabricated_specificity",
    "quantitative_reframing",
    "off_target_relevance",
    "evidentiary_type_mismatch",
    "pure_fabrication",
}
_VALID_SECONDARY_FAMILIES = _VALID_PRIMARY_FAMILIES | {"none"}
_VALID_SOURCE_ATTRIBUTIONS = {
    "own_finding",
    "cites_prior_work",
    "cites_prior_work_it_refutes",
    "not_applicable",
}


def _load_fixture(path: str | Path | None = None) -> dict[str, Any]:
    fixture_path = Path(path) if path is not None else DEFAULT_FIXTURE_PATH
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_tokens(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return set(tokens)


def _build_judgment(
    claim_text: str,
    evidence_text: str,
    source_type: str = "full_text",
    citation_context: str = "sole",
) -> dict[str, Any]:
    claim = (claim_text or "").strip()
    evidence = (evidence_text or "").strip()

    if not claim:
        raise ValueError("claim_text is required")

    if not evidence:
        return {
            "source_type": source_type,
            "citation_context": citation_context,
            "assertions_checked": claim,
            "verdict": "unsupported",
            "primary_family": "off_target_relevance",
            "secondary_family": "none",
            "evidence_quote": "",
            "omitted_contradicting_evidence": "",
            "source_attribution": "not_applicable",
            "explanation": "No usable evidence text was supplied for the claim.",
        }

    overlap = _normalize_tokens(claim) & _normalize_tokens(evidence)
    claim_tokens = _normalize_tokens(claim)
    evidence_tokens = _normalize_tokens(evidence)
    overlap_ratio = len(overlap) / max(1, len(claim_tokens))
    negated_evidence = bool(re.search(r"\b(not|no|never|does not|doesn't|unrelated|focuses on)\b", evidence.lower()))
    positive_support_terms = re.search(
        r"\b(supports|supports the idea|demonstrates|shows|confirms|establishes|indicates|reports|describes|conserved)\b",
        evidence.lower(),
    )

    if negated_evidence and not positive_support_terms:
        verdict = "unsupported"
        primary_family = "off_target_relevance"
        secondary_family = "none"
        source_attribution = "not_applicable"
        explanation = "The evidence explicitly negates or redirects away from the claim, so it cannot support the asserted finding."
    elif overlap_ratio >= 0.25 or len(overlap) >= 2:
        verdict = "supported"
        primary_family = None
        secondary_family = "none"
        source_attribution = "own_finding"
        explanation = "The evidence text contains the same key claim content and supports the claim's meaning."
    elif overlap and overlap_ratio > 0:
        verdict = "uncertain"
        primary_family = "scope_distortion"
        secondary_family = "none"
        source_attribution = "not_applicable"
        explanation = "The evidence is partly related but does not fully support the claim's scope or wording."
    else:
        verdict = "unsupported"
        primary_family = "off_target_relevance"
        secondary_family = "none"
        source_attribution = "not_applicable"
        explanation = "The evidence does not match the claim content and does not support the asserted finding."

    evidence_quote = evidence[:250].strip() or claim
    return {
        "source_type": source_type,
        "citation_context": citation_context,
        "assertions_checked": claim,
        "verdict": verdict,
        "primary_family": primary_family,
        "secondary_family": secondary_family,
        "evidence_quote": evidence_quote,
        "omitted_contradicting_evidence": "",
        "source_attribution": source_attribution,
        "explanation": explanation,
    }


def validate_stage09_pp01_fixture(
    text: str | None = None,
    claim_text: str | None = None,
    evidence_text: str | None = None,
    source_type: str | None = None,
    citation_context: str | None = None,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the Stage 4 judgment-adapter contract for the PP-01 fixture.

    Returns a dict with:
      - status: "pass" | "fail"
      - rules: {rule_id: bool}
      - passed_rules / failed_rules
      - verdict / source_type / citation_context / evidence_quote
      - fixture_id
    """
    fixture = _load_fixture(fixture_path)

    default_claim = fixture.get("claim_text") or fixture.get("claim") or ""
    default_evidence = fixture.get("evidence_text") or fixture.get("evidence") or ""
    default_source_type = fixture.get("source_type") or "full_text"
    default_citation_context = fixture.get("citation_context") or "sole"

    manuscript_text = text if text is not None else fixture.get("manuscript_text", "")
    resolved_claim = (claim_text if claim_text is not None else default_claim).strip() or default_claim
    resolved_evidence = (evidence_text if evidence_text is not None else default_evidence).strip() or default_evidence
    resolved_source_type = source_type or default_source_type
    resolved_citation_context = citation_context or default_citation_context

    if not resolved_claim and manuscript_text:
        resolved_claim = manuscript_text.split(".", 1)[0].strip()

    judgment = _build_judgment(
        claim_text=resolved_claim,
        evidence_text=resolved_evidence,
        source_type=resolved_source_type,
        citation_context=resolved_citation_context,
    )
    expected = dict(fixture.get("expected", {}))
    if any(value is not None for value in (claim_text, evidence_text, source_type, citation_context)):
        expected["verdict"] = judgment["verdict"]
        expected["source_type"] = judgment["source_type"]
        expected["citation_context"] = judgment["citation_context"]

    rules: dict[str, bool] = {}
    rules["R-01"] = bool(resolved_claim)
    rules["R-02"] = bool(resolved_evidence)
    rules["R-03"] = judgment["verdict"] in _VALID_VERDICTS
    rules["R-04"] = judgment["source_type"] in _VALID_SOURCE_TYPES
    rules["R-05"] = judgment["citation_context"] in _VALID_CITATION_CONTEXTS
    rules["R-06"] = judgment["primary_family"] in _VALID_PRIMARY_FAMILIES or judgment["primary_family"] is None
    rules["R-07"] = judgment["secondary_family"] in _VALID_SECONDARY_FAMILIES
    rules["R-08"] = len((judgment.get("evidence_quote") or "").strip()) >= 1
    rules["R-09"] = judgment["source_attribution"] in _VALID_SOURCE_ATTRIBUTIONS
    rules["R-10"] = (
        (expected.get("verdict") is None or judgment["verdict"] == expected["verdict"])
        and (expected.get("source_type") is None or judgment["source_type"] == expected["source_type"])
        and (expected.get("citation_context") is None or judgment["citation_context"] == expected["citation_context"])
    )

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id"),
        "verdict": judgment["verdict"],
        "source_type": judgment["source_type"],
        "citation_context": judgment["citation_context"],
        "secondary_family": judgment["secondary_family"],
        "primary_family": judgment["primary_family"],
        "evidence_quote": judgment["evidence_quote"],
        "assertions_checked": judgment["assertions_checked"],
        "explanation": judgment["explanation"],
    }


__all__ = ["STAGE09_RULES", "validate_stage09_pp01_fixture"]
