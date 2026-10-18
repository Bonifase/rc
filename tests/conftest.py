"""Tests must never reach the real OpenAI API, whatever the developer's shell exports."""
import pytest


@pytest.fixture(autouse=True)
def _no_real_openai(monkeypatch, tmp_path):
    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", "0")
    monkeypatch.setenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "0")
    monkeypatch.setenv("REFCHECK_CALL_STORE", str(tmp_path / "call_store"))
    monkeypatch.delenv("REFCHECK_MAX_MODEL_CALLS", raising=False)
    from src.model_call_store import reset_budget

    reset_budget()

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to send a real OpenAI request")

    monkeypatch.setattr("openai.resources.chat.completions.Completions.create", refuse)
