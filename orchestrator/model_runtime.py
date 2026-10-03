from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from openai import OpenAI

MODEL_PRICE_PER_1K = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
    "gpt-4.1": {"input": 0.002, "output": 0.008},
}


def _default_cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prices = MODEL_PRICE_PER_1K.get(model, {"input": 0.0, "output": 0.0})
    prompt_cost = (prompt_tokens / 1000.0) * prices["input"]
    completion_cost = (completion_tokens / 1000.0) * prices["output"]
    return prompt_cost + completion_cost


class OpenAIModelClient:
    """Strict OpenAI wrapper for orchestrated model work.

    - no automatic retries from the SDK
    - timeout enforced explicitly
    - attempt cap enforced at the wrapper level
    - every call is logged to JSONL with token usage and cost
    - runs can resume by task key without re-running completed paid work
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        state_path: str | Path = "output/orchestrator_state.json",
        log_path: str | Path = "output/orchestrator_calls.jsonl",
        client_factory: Optional[Callable[[], OpenAI]] = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.state_path = Path(state_path)
        self.log_path = Path(log_path)
        self._client_factory = client_factory or (lambda: OpenAI(api_key=api_key, max_retries=0, timeout=timeout_seconds))
        self._lock = threading.RLock()
        self._state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2, sort_keys=True), encoding="utf-8")

    def _log_call(self, *, task_key: str, attempt: int, status: str, **payload: Any) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "task_key": task_key,
            "attempt": attempt,
            "status": status,
            "model": self.model,
            "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        record.update(payload)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")

    def _mark_completed(self, task_key: str, result: Any) -> None:
        with self._lock:
            self._state[task_key] = {"status": "completed", "result": result}
            self._save_state()

    def _is_completed(self, task_key: str) -> bool:
        with self._lock:
            rec = self._state.get(task_key)
            return bool(rec and rec.get("status") == "completed")

    def _call_openai(self, *, task_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._client_factory()
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    temperature=0,
                    max_tokens=512,
                    timeout=self.timeout_seconds,
                )
                usage = getattr(response, "usage", None)
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                cached_tokens = int(getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0)
                cost_usd = _default_cost_for(self.model, prompt_tokens, completion_tokens)
                content = response.choices[0].message.content if getattr(response, "choices", None) else ""
                response_payload = {"content": content, "status": "success"}
                self._log_call(
                    task_key=task_key,
                    attempt=attempt,
                    status="success",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached_tokens=cached_tokens,
                    cost_usd=cost_usd,
                    raw_response=response_payload,
                )
                return {"ok": True, "response": response_payload, "cost_usd": cost_usd}
            except Exception as exc:  # no retries at the SDK or wrapper level beyond the attempt cap
                self._log_call(
                    task_key=task_key,
                    attempt=attempt,
                    status="error",
                    error=str(exc),
                    attempt_cap=self.max_attempts,
                )
                if attempt >= self.max_attempts:
                    raise

    def run_task(self, task_key: str, work: Callable[[], Any]) -> Any:
        if self._is_completed(task_key):
            with self._lock:
                return self._state[task_key]["result"]

        result = work()
        self._mark_completed(task_key, result)
        return result

    def run_model_call(self, task_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._is_completed(task_key):
            with self._lock:
                return self._state[task_key]["result"]

        result = self._call_openai(task_key=task_key, payload=payload)
        self._mark_completed(task_key, result)
        return result
