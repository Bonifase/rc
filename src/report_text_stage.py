"""Report-text stage for unsupported citation cases.

This module is intentionally deterministic and offline-safe. It runs only after a
final verdict has already been produced, and it never feeds its output back into
that verdict logic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]


def _candidate_prompt_paths() -> list[Path]:
    return [
        ROOT / "prompts" / "report_text_v2.txt",
        ROOT / "REPORT_STAGE_FINAL_WITH_METADATA" / "prompts" / "report_text_v2.txt",
    ]


def load_report_text_prompt(prompt_path: str | Path | None = None) -> str:
    candidates: list[Path] = []
    if prompt_path is not None:
        candidates.append(Path(prompt_path))
    candidates.extend(_candidate_prompt_paths())
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return "Report-text prompt not available in the local offline bundle."


def _default_error_types() -> list[dict[str, str]]:
    fixture_path = ROOT / "REPORT_STAGE_FINAL_WITH_METADATA" / "fixtures" / "content_error_types_en.json"
    if fixture_path.exists():
        with fixture_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    return [
        {"key": "source_says_opposite", "label": "The source says the opposite", "description": "The cited work says the opposite."},
        {"key": "study_did_not_do_that", "label": "The study did not do that", "description": "The study did not have the described design or scope."},
        {"key": "data_mismatch", "label": "Data does not match the study", "description": "The study reports a different entity."},
        {"key": "inflated_figure", "label": "Inflated figure", "description": "The reported figure is larger than the source."},
        {"key": "figure_out_of_context", "label": "Figure taken out of context", "description": "The figure is used for a different context."},
        {"key": "measure_confused", "label": "One measure confused with another", "description": "The measure in the sentence is not the one in the source."},
        {"key": "source_does_not_say", "label": "The source does not say that", "description": "The source does not report this claim."},
        {"key": "overstated_certainty", "label": "Overstated certainty", "description": "The sentence treats a suggestion as a fact."},
    ]


def build_report_text_input(
    *,
    claim: str,
    marker: str,
    evidence: list[dict[str, Any]],
    source_type: str,
    citation_context: str,
    judge_quotes: Iterable[str],
    judge_explanations: Iterable[str],
    error_types: list[dict[str, str]] | None = None,
    report_language: str = "en",
) -> dict[str, Any]:
    return {
        "report_language": report_language,
        "citation_context": citation_context,
        "marker": marker,
        "claim": claim,
        "source_type": source_type,
        "evidence": evidence,
        "judge_quotes": list(judge_quotes),
        "judge_explanations": list(judge_explanations),
        "error_types": error_types or _default_error_types(),
    }


def _mock_report_text_reply(report_input: dict[str, Any]) -> dict[str, str | None]:
    claim = str(report_input["claim"])
    evidence_text = "\n".join(str(item.get("text", "")) for item in report_input.get("evidence", []))
    evidence_text_clean = evidence_text.strip()
    selected_type = next((item["key"] for item in report_input["error_types"] if item["key"] == "source_does_not_say"), "source_does_not_say")
    claim_marked = f"**{claim}**" if claim else "**the claim**"
    evidence_passage = f"**{evidence_text_clean[:240]}**" if evidence_text_clean else "**the supplied source excerpt**"
    evidence_context = None if report_input["source_type"] == "full_text" else "This study reports the relevant experimental result and uses a design consistent with the evidence provided."
    observation = "The source does not report the sentence's central claim. The evidence supports a narrower or different finding, so the manuscript statement overstates what the cited work demonstrates."
    return {
        "error_type": selected_type,
        "claim_marked": claim_marked,
        "evidence_passage": evidence_passage,
        "evidence_context": evidence_context,
        "observation": observation,
    }


def _report_text_validator():
    validator_path = ROOT / "reference" / "validate_report_text.py"
    if not validator_path.exists():
        return None
    import importlib.util

    spec = importlib.util.spec_from_file_location("ref_validate_report_text", validator_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "validate", None)


def maybe_run_report_text_stage(
    *,
    claim: str,
    marker: str,
    evidence: list[dict[str, Any]],
    source_type: str,
    citation_context: str,
    final_verdict: str,
    judge_quotes: list[str] | None = None,
    judge_explanations: list[str] | None = None,
    show_content: bool = True,
    prompt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the report-text stage only after a final unsupported verdict.

    The stage is intentionally non-mutating: it records a report payload and is
    used for author-facing explanation only; it never changes the verdict.
    """
    if final_verdict != "unsupported":
        return {
            "status": "skipped",
            "report_complete": False,
            "verdict_preserved": final_verdict,
            "reason": "report text runs only for unsupported verdicts",
        }
    if not show_content:
        return {
            "status": "skipped",
            "report_complete": False,
            "verdict_preserved": final_verdict,
            "reason": "show_content is false for this reference",
        }

    report_input = build_report_text_input(
        claim=claim,
        marker=marker,
        evidence=evidence,
        source_type=source_type,
        citation_context=citation_context,
        judge_quotes=judge_quotes or [],
        judge_explanations=judge_explanations or [],
    )
    prompt_text = load_report_text_prompt(prompt_path)
    reply = _mock_report_text_reply(report_input)

    validator = _report_text_validator()
    if validator is not None:
        errors = validator(report_input, reply)
        if errors:
            return {
                "status": "report_text_failed",
                "report_complete": False,
                "verdict_preserved": final_verdict,
                "reason": "report text validation failed",
                "input": report_input,
                "reply": reply,
                "errors": errors,
                "prompt_path": str(prompt_path) if prompt_path else str(_candidate_prompt_paths()[0]),
                "prompt_sha256": __import__("hashlib").sha256(prompt_text.encode("utf-8")).hexdigest(),
            }

    return {
        "status": "ready",
        "report_complete": True,
        "verdict_preserved": final_verdict,
        "input": report_input,
        "reply": reply,
        "prompt_path": str(prompt_path) if prompt_path else str(_candidate_prompt_paths()[0]),
        "prompt_sha256": __import__("hashlib").sha256(prompt_text.encode("utf-8")).hexdigest(),
    }


__all__ = [
    "build_report_text_input",
    "load_report_text_prompt",
    "maybe_run_report_text_stage",
]
