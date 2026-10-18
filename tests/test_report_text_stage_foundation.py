import json

from reference.validate_report_text import validate
from src.report_text_stage import (REPORT_TEXT_MODEL_SETTINGS,
                                   build_report_text_input,
                                   collect_unsupported_sol_votes,
                                   maybe_run_report_text_stage,
                                   request_report_text)


def test_build_report_text_input_has_exact_contract_and_deduplicates_vote_inputs():
    votes = [
        {
            "status": "unsupported",
            "response": {
                "evidence_quotes": ["alpha", "beta"],
                "explanation": "first explanation",
            },
        },
        {
            "status": "unsupported",
            "response": {
                "evidence_quotes": ["beta", "gamma"],
                "explanation": "second explanation",
            },
        },
        {
            "status": "unsupported",
            "response": {
                "evidence_quotes": ["alpha"],
                "explanation": "first explanation",
            },
        },
    ]

    quotes, explanations = collect_unsupported_sol_votes(votes)
    assert quotes == ["alpha", "beta", "gamma"]
    assert explanations == ["first explanation", "second explanation"]

    report_input = build_report_text_input(
        claim="Sentence with a citation.",
        marker="[2]",
        evidence=[{"location": "body", "text": "alpha beta gamma"}],
        source_type="full_text",
        citation_context="sole",
        judge_quotes=quotes,
        judge_explanations=explanations,
        error_types=[{"key": "source_does_not_say", "label": "source does not say", "description": "desc"}],
    )

    assert list(report_input.keys()) == [
        "report_language",
        "citation_context",
        "marker",
        "claim",
        "source_type",
        "evidence",
        "judge_quotes",
        "judge_explanations",
        "error_types",
    ]
    assert report_input["judge_quotes"] == ["alpha", "beta", "gamma"]
    assert report_input["judge_explanations"] == ["first explanation", "second explanation"]


def test_report_text_provider_uses_luna_settings_and_retries_once():
    audit_ledger = []
    attempts = {"count": 0}
    base_report_input = json.loads(open("fixtures/case_02_input.json", "r", encoding="utf-8").read())

    def fake_provider(**kwargs):
        attempts["count"] += 1
        report_input = kwargs["report_input"]
        if attempts["count"] == 1:
            return {
                "error_type": "source_does_not_say",
                "claim_marked": "**" + report_input["claim"] + "**",
                "evidence_passage": "**bad**",
                "evidence_context": None,
                "observation": "The source does not say this claim.",
            }
        return {
            "error_type": "source_says_opposite",
            "claim_marked": "Knockout work in zebrafish larvae mapped the division of labour between the two: **MR-null animals were hypercortisolemic and failed to mount any cortisol response to a stressor**, whereas **GR-null animals retained a delayed but sustained response**, indicating that **the mineralocorticoid receptor rather than the glucocorticoid receptor sets the negative-feedback set-point** of the stress axis [2].",
            "evidence_passage": "Despite the high maternal cortisol deposition in GR−/− embryos, this steroid is cleared rapidly, and the low cortisol levels are maintained for 24 h. While the mechanism is unclear as 11β-hsd2 transcript levels are low in GR−/− larvae (Fig. 2A), the excretion of this excess steroid may involve either diffusion36, activation of other biotransformation pathways37 and/or upregulation of efflux transporters38. Except for this brief period, **GR−/−, but not MR−/−mutants were hypercortisolemic** and the steroid levels were beyond what could be induced by a stressor in the WT larvae (Fig. 2A)14. This suggests either a non-functional negative feedback mechanism and/or a lack of cortisol breakdown in the GR mutant. In contrast to GR, **a loss of MR resulted in basal cortisol levels similar to WT** (Fig. 2A), **supporting a key role for GR signalling in maintaining the steady-state basal glucocorticoid levels** during early development32, and this may involve a tight negative feedback regulation in fish.",
            "evidence_context": None,
            "observation": "The full text reports the opposite of the sentence. It is the glucocorticoid receptor mutants, not the mineralocorticoid receptor mutants, that are hypercortisolemic, with cortisol levels beyond what a stressor induces in wild-type larvae, while loss of the mineralocorticoid receptor leaves basal cortisol similar to wild type. The authors attribute basal cortisol control and negative feedback to the glucocorticoid receptor, so the sentence swaps the two receptors and, with them, its conclusion about the feedback set-point.",
        }

    output = request_report_text(
        base_report_input,
        provider=fake_provider,
        case_id="CASE-RETRY",
        audit_ledger=audit_ledger,
        max_attempts=2,
    )

    assert output["status"] == "ready"
    assert attempts["count"] == 2
    assert len(audit_ledger) == 2
    assert {entry["stage"] for entry in audit_ledger} == {"report_text"}
    assert {entry["call_key"] for entry in audit_ledger} == {
        "CASE-RETRY::gpt-5.6-luna::report_text::1",
        "CASE-RETRY::gpt-5.6-luna::report_text::2",
    }
    assert REPORT_TEXT_MODEL_SETTINGS["model"] == "gpt-5.6-luna"
    assert REPORT_TEXT_MODEL_SETTINGS["timeout_seconds"] == 60
    assert REPORT_TEXT_MODEL_SETTINGS["max_completion_tokens"] == 8000
    assert REPORT_TEXT_MODEL_SETTINGS["max_retries"] == 0


def test_report_text_stage_marks_failed_after_two_invalid_attempts():
    def fake_provider(**kwargs):
        return {
            "error_type": "source_does_not_say",
            "claim_marked": "**different claim**",
            "evidence_passage": "**beta**",
            "evidence_context": None,
            "observation": "The source does not say this claim.",
        }

    result = maybe_run_report_text_stage(
        claim="Sentence with a citation.",
        marker="[2]",
        evidence=[{"location": "body", "text": "alpha beta gamma"}],
        source_type="full_text",
        citation_context="sole",
        final_verdict="unsupported",
        judge_quotes=["alpha"],
        judge_explanations=["The source mentions alpha only."],
        provider=fake_provider,
        case_id="CASE-FAIL",
        audit_ledger=[],
        max_attempts=2,
    )

    assert result["status"] == "report_text_failed"
    assert result["attempts"] == 2
    assert result["audit_ledger"][-1]["attempt_number"] == 2


def test_invalid_report_text_response_is_rejected_for_unknown_error_type_and_invented_number():
    report_input = json.loads(open("fixtures/case_02_input.json", "r", encoding="utf-8").read())

    invalid_type = dict(json.loads(open("fixtures/case_02_mock_reply.json", "r", encoding="utf-8").read()))
    invalid_type["error_type"] = "unknown_category"
    assert validate(report_input, invalid_type)

    invented_number = dict(json.loads(open("fixtures/case_02_mock_reply.json", "r", encoding="utf-8").read()))
    invented_number["observation"] = "The source says 9999 is the answer."
    assert validate(report_input, invented_number)
