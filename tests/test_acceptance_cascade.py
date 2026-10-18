import json
from pathlib import Path

import pytest

from src.acceptance_harness import (_EVIDENCE, _NON_LITERAL_QUOTE,
                                    _ScriptedVoteProvider,
                                    expected_final_verdict,
                                    orchestrator_accept,
                                    verify_case_consistency)
from src.hybrid_judge_adapter import run_hybrid_luna_sol_cascade
from src.report_text_stage import request_report_text

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = [{"location": "abstract", "text": _EVIDENCE}]
CLAIM = "Exercise reduced CRP and IL-6 after 12 weeks."


def _cascade(script, case_id="T-CASE"):
    provider = _ScriptedVoteProvider(script)
    result = run_hybrid_luna_sol_cascade(CLAIM, EVIDENCE, "abstract", "sole", case_id=case_id, provider=provider)
    return result, provider


def _fixture(name):
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


def test_luna_six_of_six_clears_without_calling_sol():
    result, provider = _cascade({"luna": ["supported"] * 6, "sol": []})

    assert result["final_verdict"] == "supported"
    assert result["escalated"] is False
    assert [call[0] for call in provider.calls] == ["gpt-5.6-luna"] * 6
    assert verify_case_consistency(result) == []


@pytest.mark.parametrize("luna", [
    ["supported"] * 5 + ["unsupported"],
    ["supported"] * 5 + ["uncertain"],
    ["unsupported"] * 6,
])
def test_any_luna_vote_that_is_not_supported_escalates_to_six_sol_votes(luna):
    result, provider = _cascade({"luna": luna, "sol": ["unsupported"] * 6})

    models = [call[0] for call in provider.calls]
    assert models == ["gpt-5.6-luna"] * 6 + ["gpt-5.6-sol"] * 6
    assert result["escalated"] is True
    assert result["sol_votes"] == ["unsupported"] * 6
    assert result["final_verdict"] == "unsupported"
    assert verify_case_consistency(result) == []


@pytest.mark.parametrize("sol,expected", [
    (["unsupported"] * 4 + ["supported"] * 2, "unsupported"),
    (["unsupported"] * 3 + ["supported"] * 3, "uncertain"),
    (["supported"] * 4 + ["uncertain"] * 2, "supported"),
    (["unsupported"] * 3 + ["uncertain"] * 3, "uncertain"),
])
def test_sol_needs_four_votes_for_a_final_decision(sol, expected):
    result, _ = _cascade({"luna": ["unsupported"] * 6, "sol": sol})

    assert result["final_verdict"] == expected
    assert expected == expected_final_verdict(["unsupported"] * 6, sol)


def test_non_literal_quote_is_rejected_then_retried_once_with_its_own_call_key():
    luna = ["nonliteral:supported"] + ["supported"] * 5
    result, provider = _cascade({"luna": luna, "sol": []})

    slot_one = [row for row in result["call_log"] if row["model"] == "gpt-5.6-luna" and row["vote_slot"] == 1]
    assert [row["attempt_number"] for row in slot_one] == [1, 2]
    assert slot_one[0]["validation_status"] == "failed" and slot_one[0]["failure_category"] == "invalid_vote"
    assert slot_one[0]["vote_response"]["evidence_quotes"] == [_NON_LITERAL_QUOTE]
    assert slot_one[1]["validation_status"] == "valid"
    assert slot_one[0]["call_key"] != slot_one[1]["call_key"]
    assert result["final_verdict"] == "supported"
    assert len(provider.calls) == 7
    assert verify_case_consistency(result) == []


def test_a_vote_whose_quote_stays_non_literal_is_invalid_and_never_counts_as_unsupported():
    class AlwaysNonLiteral:
        def request_vote(self, *, model, vote_slot, attempt_number):
            return {"evidence_sufficient": True, "material_error_present": True,
                    "evidence_quotes": [_NON_LITERAL_QUOTE], "explanation": "invented quote"}

    result = run_hybrid_luna_sol_cascade(CLAIM, EVIDENCE, "abstract", "sole", case_id="T-NL", provider=AlwaysNonLiteral())

    assert result["luna_votes"] == ["failed"] * 6
    assert result["sol_votes"] == ["failed"] * 6
    assert result["final_verdict"] == "uncertain"
    assert not any(row["validation_status"] == "valid" for row in result["call_log"])
    assert max(row["attempt_number"] for row in result["call_log"]) == 2


def test_schema_failures_are_not_retried():
    class MissingField:
        def __init__(self):
            self.calls = 0

        def request_vote(self, *, model, vote_slot, attempt_number):
            self.calls += 1
            return {"evidence_sufficient": True, "material_error_present": False, "evidence_quotes": [_EVIDENCE]}

    provider = MissingField()
    run_hybrid_luna_sol_cascade(CLAIM, EVIDENCE, "abstract", "sole", case_id="T-SCHEMA", provider=provider)

    assert provider.calls == 12  # six Luna and six Sol slots, one attempt each


def test_harness_verdicts_follow_from_their_own_votes():
    result = orchestrator_accept(mode="replay")

    assert result["status"] == "accepted"
    for item in result["verdicts"]:
        assert item["problems"] == [], item["case_id"]
        assert item["final_verdict"] == expected_final_verdict(item["luna_votes"], item["sol_votes"])
    by_id = {item["case_id"]: item for item in result["verdicts"]}
    assert by_id["PP01-CASE-01"]["escalated"] is False and by_id["PP01-CASE-01"]["sol_votes"] == []
    assert by_id["PP01-CASE-02"]["escalated"] is True and by_id["PP01-CASE-02"]["sol_votes"] == ["unsupported"] * 6
    assert any(row["failure_category"] == "invalid_vote" for row in by_id["PP01-CASE-03"]["call_log"])
    assert not any(row["paid"] for item in result["verdicts"] for row in item["call_log"])


def test_consistency_check_rejects_a_verdict_the_votes_do_not_support():
    item = orchestrator_accept(mode="replay")["verdicts"][1]  # six Luna unsupported, six Sol unsupported

    forged = {**item, "final_verdict": "supported"}
    assert any("votes decide" in problem for problem in verify_case_consistency(forged))

    no_sol = {**item, "sol_votes": [], "call_log": [row for row in item["call_log"] if row["model"] == "gpt-5.6-luna"]}
    assert any("Sol did not fill six vote slots" in problem for problem in verify_case_consistency(no_sol))

    duplicated = {**item, "call_log": item["call_log"] + [item["call_log"][0]]}
    assert any("duplicate call keys" in problem for problem in verify_case_consistency(duplicated))

    bad_quote = json.loads(json.dumps(item))
    bad_quote["call_log"][0]["vote_response"]["evidence_quotes"] = [_NON_LITERAL_QUOTE]
    assert any("non-literal" in problem for problem in verify_case_consistency(bad_quote))


def test_invalid_report_text_attempts_are_logged_as_invalid_not_ok():
    report_input = _fixture("case_02_input.json")
    invalid = _fixture("case_02_invalid_reply.json")
    valid = _fixture("case_02_mock_reply.json")
    replies = iter([invalid, valid])

    def provider(**_kwargs):
        return next(replies)

    result = request_report_text(report_input, provider=provider, case_id="T::ref1")

    assert result["status"] == "ready"
    assert [row["status"] for row in result["audit_ledger"]] == ["invalid", "ok"]
    assert result["audit_ledger"][0]["validation_errors"]
    assert result["audit_ledger"][0]["call_key"] != result["audit_ledger"][1]["call_key"]


def test_two_invalid_report_text_attempts_fail_with_both_rows_invalid():
    report_input = _fixture("case_02_input.json")
    invalid = _fixture("case_02_invalid_reply.json")

    result = request_report_text(report_input, provider=lambda **_kwargs: invalid, case_id="T::ref2")

    assert result["status"] == "report_text_failed"
    assert [row["status"] for row in result["audit_ledger"]] == ["invalid", "invalid"]
