from src.report_text_stage import final_vote_quotes


def _entry(model, verdict, quotes, status="valid"):
    return {"model": model, "verdict": verdict, "validation_status": status,
            "vote_response": {"evidence_quotes": quotes}}


def test_sol_quotes_preferred_for_final_verdict():
    payload = {"final_verdict": "unsupported", "call_log": [
        _entry("gpt-5.6-luna", "unsupported", ["luna quote"]),
        _entry("gpt-5.6-sol", "unsupported", ["sol quote"]),
        _entry("gpt-5.6-sol", "supported", ["other quote"]),
    ]}
    assert final_vote_quotes(payload) == ["sol quote"]


def test_luna_quotes_used_when_sol_not_invoked():
    payload = {"final_verdict": "supported", "call_log": [
        _entry("gpt-5.6-luna", "supported", ["luna quote", "luna quote"]),
        _entry("gpt-5.6-luna", "supported", ["bad"], status="failed"),
    ]}
    assert final_vote_quotes(payload) == ["luna quote"]


def test_no_quotes_returns_empty():
    assert final_vote_quotes({"final_verdict": "uncertain", "call_log": []}) == []
    assert final_vote_quotes(None) == []
