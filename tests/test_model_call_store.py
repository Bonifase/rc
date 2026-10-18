import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.estimate import estimate_run
from src.hybrid_judge_adapter import _OpenAIVoteProvider
from src.model_call_store import (FatalModelCallError, ModelCallBudgetExceeded,
                                  cached_call, reset_budget, store_dir)
from src.report_text_stage import maybe_run_report_text_stage

ROOT = Path(__file__).resolve().parents[1]


def _send(counter, text="reply"):
    def send():
        counter.append(1)
        return text, {"prompt_tokens": 10, "completion_tokens": 5}

    return send


def test_a_repeated_call_is_replayed_from_disk_not_sent_again():
    sent = []

    first = cached_call(model="m", system="s", user="u", slot="vote1", send=_send(sent))
    second = cached_call(model="m", system="s", user="u", slot="vote1", send=_send(sent))

    assert first == ("reply", {"prompt_tokens": 10, "completion_tokens": 5}, False)
    assert second == ("reply", {"prompt_tokens": 10, "completion_tokens": 5}, True)
    assert len(sent) == 1


def test_votes_that_share_an_input_are_stored_separately():
    sent = []
    for slot in ("vote1", "vote2", "vote1"):
        cached_call(model="m", system="s", user="u", slot=slot, send=_send(sent))

    assert len(sent) == 2


def test_the_raw_response_is_on_disk_and_listed_in_the_ledger():
    cached_call(model="m", system="s", user="u", slot="vote1", send=_send([], text="not even json"))

    saved = [path for path in store_dir().glob("*.json")]
    assert json.loads(saved[0].read_text(encoding="utf-8"))["response_text"] == "not even json"
    ledger = [json.loads(line) for line in (store_dir() / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert ledger[0]["cached"] is False and ledger[0]["prompt_tokens"] == 10


def test_the_call_budget_stops_the_run_and_replays_do_not_count(monkeypatch):
    monkeypatch.setenv("REFCHECK_MAX_MODEL_CALLS", "2")
    reset_budget()
    sent = []
    cached_call(model="m", system="s", user="u", slot="a", send=_send(sent))
    cached_call(model="m", system="s", user="u", slot="b", send=_send(sent))
    cached_call(model="m", system="s", user="u", slot="a", send=_send(sent))  # replay, free

    with pytest.raises(ModelCallBudgetExceeded, match="re-run"):
        cached_call(model="m", system="s", user="u", slot="c", send=_send(sent))
    assert len(sent) == 2


class FakeClient:
    def __init__(self, content):
        self.requests = []
        self._content = content
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))],
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30),
        )


VOTE = json.dumps({
    "evidence_sufficient": True, "material_error_present": True,
    "evidence_quotes": ["no reduction in CRP"], "explanation": "No reduction.",
})
JUDGE_INPUT = {
    "citation_context": "sole", "claim": "CRP fell.", "source_type": "abstract",
    "evidence": [{"location": "abstract", "text": "The study reported no reduction in CRP."}],
}
CONFIG = {"timeout_seconds": 60, "max_completion_tokens": 8000}


def test_a_rerun_of_the_same_votes_makes_no_new_requests():
    client = FakeClient(VOTE)
    for _ in range(2):
        provider = _OpenAIVoteProvider(client)
        provider.request_vote(model="gpt-5.6-luna", vote_slot=1, attempt_number=1, judge_input=JUDGE_INPUT, prompt_text="P", request_config=CONFIG)

    assert len(client.requests) == 1
    assert provider.last_cached is True


def test_report_text_replays_after_a_restart_and_is_marked_as_not_paid(monkeypatch):
    report_input = json.loads((ROOT / "fixtures" / "case_02_input.json").read_text(encoding="utf-8"))
    reply = (ROOT / "fixtures" / "case_02_mock_reply.json").read_text(encoding="utf-8")
    client = FakeClient(reply)
    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", "1")
    monkeypatch.setattr("src.config.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("src.llm_wrapper._get_client", lambda: client)

    def run():
        return maybe_run_report_text_stage(
            claim=report_input["claim"], marker=report_input["marker"], evidence=report_input["evidence"],
            source_type=report_input["source_type"], citation_context=report_input["citation_context"],
            final_verdict="unsupported", judge_quotes=report_input["judge_quotes"],
            judge_explanations=report_input["judge_explanations"], case_id="s1::ref2",
        )

    first, second = run(), run()

    assert len(client.requests) == 1
    assert first["status"] == second["status"] == "ready"
    assert [row["paid"] for row in (first["audit_ledger"][0], second["audit_ledger"][0])] == [True, False]


def test_a_rejected_report_text_request_stops_the_run_instead_of_failing_every_pair(monkeypatch):
    class Rejected(Exception):
        status_code = 401

    class Failing(FakeClient):
        def create(self, **kwargs):
            self.requests.append(kwargs)
            raise Rejected("Incorrect API key")

    client = Failing("")
    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", "1")
    monkeypatch.setattr("src.config.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("src.llm_wrapper._get_client", lambda: client)
    report_input = json.loads((ROOT / "fixtures" / "case_02_input.json").read_text(encoding="utf-8"))

    with pytest.raises(FatalModelCallError, match="HTTP 401"):
        maybe_run_report_text_stage(
            claim=report_input["claim"], marker=report_input["marker"], evidence=report_input["evidence"],
            source_type=report_input["source_type"], citation_context=report_input["citation_context"],
            final_verdict="unsupported", judge_quotes=report_input["judge_quotes"],
            judge_explanations=report_input["judge_explanations"],
        )
    assert len(client.requests) == 1


def _docx(body):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>')
        archive.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{body}</w:t></w:r></w:p></w:body></w:document>')
    return buffer.getvalue()


def test_the_estimate_counts_calls_without_any_model_request(tmp_path):
    source = tmp_path / "paper.docx"
    source.write_bytes(_docx(
        "First claim [1]. Second claim [1][2]. Third claim [9].\n\nReferences\n"
        "[1] Smith A. Paper one. 2020. https://doi.org/10.1000/one\n"
        "[2] Jones B. Paper two. 2021. https://doi.org/10.1000/two"
    ))

    result = estimate_run(source)

    assert result["references_parsed"] == 2
    assert result["cited_pairs"] == 3
    assert result["orphan_markers"] == ["[9]"]
    assert (result["judge_calls_minimum"], result["judge_calls_maximum"], result["model_calls_maximum"]) == (18, 36, 42)
