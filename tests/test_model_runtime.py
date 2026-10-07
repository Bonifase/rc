import json

from orchestrator.model_runtime import OpenAIModelClient


class FakeOpenAI:
    def __init__(self, *, calls):
        self.calls = calls
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls["count"] += 1
        return type(
            "Response",
            (),
            {
                "usage": type(
                    "Usage",
                    (),
                    {"prompt_tokens": 50, "completion_tokens": 25, "prompt_tokens_details": type("Details", (), {"cached_tokens": 0})()},
                )(),
                "choices": [type("Choice", (), {"message": type("Message", (), {"content": "ok"})()})()],
            },
        )()


def test_run_task_writes_pending_checkpoint_before_work_runs(tmp_path):
    state_path = tmp_path / "orchestrator_state.json"
    log_path = tmp_path / "orchestrator_calls.jsonl"
    client = OpenAIModelClient(model="gpt-4o-mini", api_key="test-key", state_path=state_path, log_path=log_path)

    seen = {}

    def work():
        if state_path.exists():
            seen["status"] = json.loads(state_path.read_text(encoding="utf-8")).get("task-1", {}).get("status")
        return {"result": "done"}

    result = client.run_task("task-1", work)

    assert result == {"result": "done"}
    assert seen["status"] == "pending"
    assert client._state["task-1"]["status"] == "completed"


def test_run_task_reuses_completed_result_after_restart(tmp_path):
    state_path = tmp_path / "orchestrator_state.json"
    log_path = tmp_path / "orchestrator_calls.jsonl"
    calls = {"count": 0}

    first = OpenAIModelClient(
        model="gpt-4o-mini",
        api_key="test-key",
        state_path=state_path,
        log_path=log_path,
        client_factory=lambda: FakeOpenAI(calls=calls),
    )

    result = first.run_model_call("task-2", {"prompt": "alpha"})
    assert result["ok"] is True

    second = OpenAIModelClient(
        model="gpt-4o-mini",
        api_key="test-key",
        state_path=state_path,
        log_path=log_path,
        client_factory=lambda: FakeOpenAI(calls=calls),
    )

    cached = second.run_model_call("task-2", {"prompt": "beta"})
    assert cached == second._state["task-2"]["result"]
    assert calls["count"] == 1
