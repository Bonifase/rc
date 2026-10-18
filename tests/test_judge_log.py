import json

import pytest

from orchestrator import runner
from src.run_mode import run_mode


def _pair(**extra):
    return {
        "sentence_id": "s1", "sentence_text": "Claim [1].", "ref_number": 1, "status": "verdict",
        "verdict": "unsupported", "source_type": "full_text", "show_content": True, **extra,
    }


JUDGE_LOG = [
    {"call_key": "c::luna::vote::1::1", "model": "gpt-5.6-luna", "vote_slot": 1, "paid": True, "prompt_tokens": 100, "completion_tokens": 20, "raw_reply": "{}"},
    {"call_key": "c::sol::vote::1::1", "model": "gpt-5.6-sol", "vote_slot": 1, "paid": True, "prompt_tokens": 100, "completion_tokens": 20, "raw_reply": "{}"},
    {"call_key": "c::sol::vote::2::1", "model": "gpt-5.6-sol", "vote_slot": 2, "paid": False, "prompt_tokens": 100, "completion_tokens": 20, "raw_reply": "{}"},
]
REPORT_TEXT_LEDGER = [{"stage": "report_text", "call_key": "s1::ref1::gpt-5.6-luna::report_text::1", "model": "gpt-5.6-luna", "paid": True}]


def _report():
    pair = _pair(judge_call_log=JUDGE_LOG, report_text_result={"status": "ready", "reply": {}, "audit_ledger": REPORT_TEXT_LEDGER})
    return {"sentences": [{"sentence_id": "s1", "sentence_text": "Claim [1].", "pairs": [pair]}], "summary": {}}


def test_the_report_carries_every_judge_and_report_text_call():
    normalized = runner._normalize_library_report(_report(), [])

    assert [(row["stage"], row["model"]) for row in normalized["call_log"]] == [
        ("judge", "gpt-5.6-luna"), ("judge", "gpt-5.6-sol"), ("judge", "gpt-5.6-sol"), ("report_text", "gpt-5.6-luna"),
    ]
    assert all(row["sentence_id"] == "s1" and row["ref_number"] == 1 for row in normalized["call_log"])


def test_only_requests_actually_sent_count_as_provider_attempts():
    normalized = runner._normalize_library_report(_report(), [])

    state = runner._judge_execution_state(api_key_missing=False, simulation_mode=False, library_report=normalized)

    assert state["provider_attempts"] == 3  # the replayed Sol vote was free
    assert state["models_called"] == ["gpt-5.6-luna", "gpt-5.6-sol"]
    assert state["sol_calls"] == 2 and state["luna_calls"] == 2


def test_live_and_simulation_together_is_a_configuration_error(monkeypatch):
    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", "1")
    monkeypatch.setenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "1")

    with pytest.raises(RuntimeError, match="Enable only one"):
        run_mode()


@pytest.mark.parametrize("live,simulation,expected", [("1", "0", "live"), ("0", "1", "simulation"), ("0", "0", "off")])
def test_run_mode(monkeypatch, live, simulation, expected):
    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", live)
    monkeypatch.setenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", simulation)

    assert run_mode() == expected


def test_a_job_folder_has_its_files_written_whole_and_the_judge_log_is_jsonl(monkeypatch, tmp_path):
    from src.storage import atomic_write_text

    target = tmp_path / "run_record.json"
    atomic_write_text(target, json.dumps({"status": "running"}))
    atomic_write_text(target, json.dumps({"status": "ok"}))

    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "ok"}
    assert not list(tmp_path.glob("*.tmp"))
