import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.pipeline import PairResult, _apply_report_text_gate
from src.report_text_stage import maybe_run_report_text_stage

ROOT = Path(__file__).resolve().parents[1]


def _fixture(name):
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


class FakeClient:
    """Records every request; replies are returned in order and the last one repeats."""

    def __init__(self, *replies):
        self.requests = []
        self._replies = list(replies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        content = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30),
        )


def _live(monkeypatch, client):
    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", "1")
    monkeypatch.setattr("src.config.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("src.llm_wrapper._get_client", lambda: client)


def _stage(report_input, **kwargs):
    return maybe_run_report_text_stage(
        claim=report_input["claim"], marker=report_input["marker"], evidence=report_input["evidence"],
        source_type=report_input["source_type"], citation_context=report_input["citation_context"],
        final_verdict="unsupported", judge_quotes=report_input["judge_quotes"],
        judge_explanations=report_input["judge_explanations"], **kwargs,
    )


def test_live_report_text_is_written_by_the_model_with_the_spec_request(monkeypatch):
    report_input = _fixture("case_02_input.json")
    reply = _fixture("case_02_mock_reply.json")
    reply["observation"] = reply["observation"] + " Model-written sentence."
    client = FakeClient(json.dumps(reply))
    _live(monkeypatch, client)

    result = _stage(report_input, case_id="s1::ref2")

    assert result["status"] == "ready" and result["simulated"] is False
    assert result["reply"]["observation"].endswith("Model-written sentence.")
    request = client.requests[0]
    assert request["model"] == "gpt-5.6-luna"
    assert request["messages"][0] == {"role": "system", "content": (ROOT / "prompts" / "report_text_v2.txt").read_bytes().decode("utf-8")}
    sent = json.loads(request["messages"][1]["content"])
    assert request["messages"][1]["content"] == json.dumps(sent, indent=2, ensure_ascii=False)
    assert sent["claim"] == report_input["claim"] and sent["judge_quotes"] == report_input["judge_quotes"]
    assert request["timeout"] == 60 and request["max_completion_tokens"] == 8000
    assert not {"max_tokens", "temperature", "seed", "response_format", "reasoning_effort"} & set(request)
    row = result["audit_ledger"][0]
    assert (row["paid"], row["prompt_tokens"], row["completion_tokens"]) == (True, 120, 30)


def test_live_invalid_replies_retry_once_then_fail_with_distinct_call_keys(monkeypatch):
    report_input = _fixture("case_02_input.json")
    client = FakeClient(json.dumps(_fixture("case_02_invalid_reply.json")))
    _live(monkeypatch, client)

    result = _stage(report_input, case_id="s1::ref2")

    assert result["status"] == "report_text_failed"
    assert len(client.requests) == 2
    assert [row["call_key"] for row in result["audit_ledger"]] == [
        "s1::ref2::gpt-5.6-luna::report_text::1", "s1::ref2::gpt-5.6-luna::report_text::2",
    ]


def test_live_non_json_reply_is_a_failed_attempt_not_a_canned_report(monkeypatch):
    client = FakeClient("I cannot help with that.")
    _live(monkeypatch, client)

    result = _stage(_fixture("case_02_input.json"))

    assert result["status"] == "report_text_failed"
    assert result["reply"] == {}


def test_no_provider_and_no_opt_in_never_produces_canned_text():
    with pytest.raises(RuntimeError, match="No report-text provider"):
        _stage(_fixture("case_02_input.json"))


def test_live_mode_without_a_key_stops_instead_of_simulating(monkeypatch):
    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", "1")
    monkeypatch.setattr("src.config.OPENAI_API_KEY", "")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        _stage(_fixture("case_02_input.json"))


def test_canned_text_only_with_the_simulation_opt_in_and_is_flagged(monkeypatch):
    monkeypatch.setenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "1")

    result = _stage(_fixture("case_02_input.json"))

    assert result["simulated"] is True
    assert result["audit_ledger"][0]["paid"] is False


def test_gate_gives_every_pair_its_own_report_text_call_key(monkeypatch):
    client = FakeClient(json.dumps(_fixture("case_02_mock_reply.json")))
    _live(monkeypatch, client)
    report_input = _fixture("case_02_input.json")

    def pair(sentence_id, number):
        return PairResult(
            sentence_id, report_input["claim"], number, status="verdict", verdict="unsupported",
            source_type="full_text", citation_context="sole", evidence_blocks=report_input["evidence"],
            judge_quotes=report_input["judge_quotes"], judge_explanations=report_input["judge_explanations"],
        )

    pairs = _apply_report_text_gate([pair("s1", 2), pair("s2", 2)])

    keys = [pairs[i].report_text_result["audit_ledger"][0]["call_key"] for i in (0, 1)]
    assert len(set(keys)) == 2
