"""
Basic observability over the existing LLM call log (src/call_logger.py).

Every LLM call already gets logged with a status and, as of the
duration_seconds field, a latency. This module turns that append-only log
into a small summary -- call count, error rate, latency percentiles -- so a
run's health can be checked at a glance instead of reading
output/llm_call_log.jsonl by hand.

Deliberately not a metrics *service* (no Prometheus, no counters held in
memory, no new infra): the call log is already the source of truth and is
already written regardless of outcome, so this just reads it back and
summarizes. Keeps the "15 minutes of work, no DB" spirit of call_logger.py.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.call_logger import DEFAULT_LOG_PATH

# Statuses log_llm_call actually writes (see llm_wrapper.py / pipeline.py):
# "verdict" is a successful call; anything else is a failure of some kind.
_SUCCESS_STATUSES = {"verdict"}


@dataclass
class CallLogSummary:
    total_calls: int
    error_count: int
    error_rate: float  # 0.0-1.0
    status_counts: dict[str, int] = field(default_factory=dict)
    avg_duration_seconds: Optional[float] = None
    p50_duration_seconds: Optional[float] = None
    p95_duration_seconds: Optional[float] = None
    max_duration_seconds: Optional[float] = None
    calls_missing_duration: int = 0  # calls logged before duration_seconds existed, or where timing failed


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def summarize_call_log(
    log_path: Path = DEFAULT_LOG_PATH,
    limit: Optional[int] = None,
) -> CallLogSummary:
    """
    Reads log_path and summarizes it. If limit is given, only the most
    recent `limit` lines are considered (e.g. "last 200 calls"), which keeps
    this cheap even on a log file that's grown large over many runs.

    Returns an all-zero summary (not an error) for a missing or empty log --
    "no data yet" is a normal, expected state, not a failure.
    """
    if not log_path.exists():
        return CallLogSummary(total_calls=0, error_count=0, error_rate=0.0)

    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return CallLogSummary(total_calls=0, error_count=0, error_rate=0.0)

    lines = [line for line in lines if line.strip()]
    if limit is not None:
        lines = lines[-limit:]

    status_counts: dict[str, int] = {}
    durations: list[float] = []
    missing_duration = 0
    error_count = 0
    parsed_count = 0

    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # a corrupted line shouldn't take down the whole summary

        parsed_count += 1
        status = record.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status not in _SUCCESS_STATUSES:
            error_count += 1

        duration = record.get("duration_seconds")
        if isinstance(duration, (int, float)):
            durations.append(float(duration))
        else:
            missing_duration += 1

    total = parsed_count
    durations.sort()

    return CallLogSummary(
        total_calls=total,
        error_count=error_count,
        error_rate=(error_count / total) if total else 0.0,
        status_counts=status_counts,
        avg_duration_seconds=(sum(durations) / len(durations)) if durations else None,
        p50_duration_seconds=_percentile(durations, 0.50) if durations else None,
        p95_duration_seconds=_percentile(durations, 0.95) if durations else None,
        max_duration_seconds=durations[-1] if durations else None,
        calls_missing_duration=missing_duration,
    )
