"""
Guard 4: log every LLM call in full. Per the integration note, this is the
only way to tell later whether a wrong verdict came from the model or from
a badly cut passage -- so every call gets logged regardless of outcome
(success, malformed response, or the quote guard dropping a quote).

Appends one JSON object per line to output/llm_call_log.jsonl. Plain
append-only file logging rather than a database -- matches the note's "15
minutes of work" framing; nothing here should block on a DB being set up.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.storage import data_dir

DEFAULT_LOG_PATH = data_dir() / "llm_call_log.jsonl"


def log_llm_call(
    *,
    claim: str,
    excerpt: str,
    source_type: str,
    citation_context: str,
    raw_response: Optional[str],
    status: str,
    error_message: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    log_path: Path = DEFAULT_LOG_PATH,
) -> None:
    """
    Appends one line. Never raises on a logging failure interrupting the
    actual pipeline -- a call that succeeded but failed to log shouldn't
    itself become a system_error the user has to deal with.

    duration_seconds is wall-clock time for the LLM call (including any
    internal retries and the quote-guard reroll), so this log doubles as the
    basic latency + error-rate record for the pipeline -- see
    src/metrics.py for the summary computed from these lines.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "claim": claim,
        "excerpt": excerpt,
        "source_type": source_type,
        "citation_context": citation_context,
        "status": status,
        "raw_response": raw_response,
        "error_message": error_message,
        "duration_seconds": duration_seconds,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        # Logging is a diagnostic aid, not a correctness requirement --
        # a disk/permissions issue here must not take down a real request.
        pass
