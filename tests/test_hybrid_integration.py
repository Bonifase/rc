from pathlib import Path

from src.hybrid_judge_adapter import (build_hybrid_judge_input,
                                      load_hybrid_prompt)
from src.report_text_stage import build_report_text_input

ROOT = Path(__file__).resolve().parents[1]


def test_hybrid_prompt_loader_reads_the_package_prompt_set():
    prompt = load_hybrid_prompt(
        "sole",
        prompt_root=ROOT / "REPORT_STAGE_FINAL_WITH_METADATA" / "judge_prompts",
    )
    assert isinstance(prompt, str)
    assert "citation_context" in prompt.lower()


def test_report_text_input_matches_the_frozen_contract():
    report_input = build_report_text_input(
        claim="Sentence with a citation.",
        marker="[2]",
        evidence=[{"location": "body", "text": "A study says this exactly."}],
        source_type="full_text",
        citation_context="sole",
        judge_quotes=["A study says this exactly."],
        judge_explanations=["The evidence provides direct support."],
        error_types=[
            {"key": "source_does_not_say", "label": "Source does not say", "description": "The source does not say this."},
            {"key": "source_says_opposite", "label": "Source says opposite", "description": "The source says the opposite."},
            {"key": "study_did_not_do_that", "label": "Study did not do that", "description": "The study did not do this."},
            {"key": "data_mismatch", "label": "Data mismatch", "description": "The data do not match."},
            {"key": "inflated_figure", "label": "Inflated figure", "description": "The figure is inflated."},
            {"key": "figure_out_of_context", "label": "Figure out of context", "description": "The figure is out of context."},
            {"key": "measure_confused", "label": "Measure confused", "description": "The measure is confused."},
            {"key": "overstated_certainty", "label": "Overstated certainty", "description": "The certainty is overstated."},
        ],
    )

    assert report_input["report_language"] == "en"
    assert report_input["citation_context"] == "sole"
    assert report_input["marker"] == "[2]"
    assert report_input["source_type"] == "full_text"
    assert "judge_quotes" in report_input
    assert "judge_explanations" in report_input


def test_hybrid_judge_input_uses_exact_four_field_contract():
    payload = build_hybrid_judge_input(
        claim="The sentence in the manuscript.",
        evidence=[{"location": "abstract", "text": "A source excerpt."}],
        source_type="abstract",
        citation_context="sole",
    )
    assert list(payload.keys()) == ["citation_context", "claim", "evidence", "source_type"]
    assert payload["citation_context"] == "sole"
