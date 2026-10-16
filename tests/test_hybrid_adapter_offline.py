from src.hybrid_judge_adapter import (build_hybrid_judge_input,
                                      run_hybrid_luna_sol_cascade)
from src.llm_wrapper import get_verdict


def test_build_hybrid_judge_input_has_exact_fields():
    payload = build_hybrid_judge_input(
        claim="A citation-bearing sentence copied verbatim from the manuscript.",
        evidence=[{"location": "abstract", "text": "This text is the exact source evidence."}],
        source_type="abstract",
        citation_context="sole",
    )

    assert list(payload.keys()) == ["citation_context", "claim", "evidence", "source_type"]
    assert payload["citation_context"] == "sole"
    assert payload["source_type"] == "abstract"
    assert payload["evidence"][0]["location"] == "abstract"


def test_run_hybrid_luna_sol_cascade_records_offline_calls():
    result = run_hybrid_luna_sol_cascade(
        claim="The exercise programme reduced inflammatory markers such as CRP and IL-6.",
        evidence=[{"location": "abstract", "text": "After 12 weeks, the exercise programme reduced plasma C-reactive protein (CRP) and interleukin-6 (IL-6)."}],
        source_type="abstract",
        citation_context="sole",
    )

    assert result["judge_executed"] is True
    assert result["final_verdict"] in {"supported", "unsupported", "uncertain"}
    assert result["call_log"]
    assert all(call["paid"] is False for call in result["call_log"])
    assert all(call["call_key"] for call in result["call_log"])


def test_get_verdict_uses_offline_hybrid_fallback_when_api_key_missing(monkeypatch):
    monkeypatch.setattr("src.llm_wrapper.OPENAI_API_KEY", "")
    monkeypatch.setattr("src.llm_wrapper.OPENAI_MODEL", "gpt-5.6-luna")

    result = get_verdict(
        "The study reduced insulin resistance.",
        "After 12 weeks, insulin resistance decreased significantly in the intervention group.",
        "abstract",
        "sole",
    )

    assert result.status == "verdict"
    assert result.verdict in {"supported", "unsupported", "uncertain"}
    assert result.source_type == "abstract"
    assert result.citation_context == "sole"
