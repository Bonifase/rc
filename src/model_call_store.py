"""Durable record of every paid model call.

The raw response is written to disk before anything validates it, so a crash, a
restart or a rejected reply never loses a paid result, and re-running the same
analysis replays saved calls instead of paying for them again.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
_LOCK = threading.Lock()
_live_calls = 0


class FatalModelCallError(RuntimeError):
    """Retrying cannot help: the provider rejected the request, or the call budget is spent."""


class ModelCallBudgetExceeded(FatalModelCallError):
    pass


def store_dir() -> Path:
    from src.storage import data_dir

    return Path(os.getenv("REFCHECK_CALL_STORE") or data_dir() / "call_store")


def reset_budget() -> None:
    global _live_calls
    with _LOCK:
        _live_calls = 0


def _spend_one() -> None:
    global _live_calls
    limit = int(os.getenv("REFCHECK_MAX_MODEL_CALLS", "0") or 0)
    with _LOCK:
        if limit and _live_calls >= limit:
            raise ModelCallBudgetExceeded(
                f"REFCHECK_MAX_MODEL_CALLS={limit} reached. Calls already made are saved in {store_dir()}; "
                "raise the limit and re-run the same manuscript to continue without paying again."
            )
        _live_calls += 1


def _key(model: str, system: str, user: str, slot: str) -> str:
    payload = json.dumps([model, system, user, slot], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ledger(directory: Path, key: str, model: str, slot: str, usage: dict[str, int], cached: bool) -> None:
    row = {"ts": time.time(), "key": key, "model": model, "slot": slot, "cached": cached, **usage}
    with _LOCK, (directory / "ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def cached_call(*, model: str, system: str, user: str, slot: str, send: Callable[[], tuple[str, dict[str, int]]]) -> tuple[str, dict[str, int], bool]:
    """Return (response_text, usage, replayed). slot distinguishes votes that share one input."""
    directory = store_dir()
    directory.mkdir(parents=True, exist_ok=True)
    key = _key(model, system, user, slot)
    path = directory / f"{key}.json"
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        _ledger(directory, key, model, slot, record.get("usage", {}), cached=True)
        return record["response_text"], record.get("usage", {}), True

    _spend_one()
    text, usage = send()
    record: dict[str, Any] = {"model": model, "slot": slot, "response_text": text, "usage": usage, "saved_at": time.time()}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    _ledger(directory, key, model, slot, usage, cached=False)
    return text, usage, False
