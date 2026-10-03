import json

from src.metrics import summarize_call_log


def _write_log(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_missing_log_returns_all_zero_summary(tmp_path):
    summary = summarize_call_log(log_path=tmp_path / "does_not_exist.jsonl")
    assert summary.total_calls == 0
    assert summary.error_count == 0
    assert summary.error_rate == 0.0
    assert summary.avg_duration_seconds is None


def test_empty_log_returns_all_zero_summary(tmp_path):
    log_path = tmp_path / "log.jsonl"
    log_path.write_text("", encoding="utf-8")
    summary = summarize_call_log(log_path=log_path)
    assert summary.total_calls == 0
    assert summary.error_rate == 0.0


def test_error_rate_counts_non_verdict_statuses_as_errors(tmp_path):
    log_path = tmp_path / "log.jsonl"
    _write_log(log_path, [
        {"status": "verdict", "duration_seconds": 1.0},
        {"status": "verdict", "duration_seconds": 2.0},
        {"status": "system_error", "duration_seconds": 3.0},
        {"status": "guard_rejected", "duration_seconds": 4.0},
    ])
    summary = summarize_call_log(log_path=log_path)
    assert summary.total_calls == 4
    assert summary.error_count == 2
    assert summary.error_rate == 0.5
    assert summary.status_counts == {"verdict": 2, "system_error": 1, "guard_rejected": 1}


def test_latency_stats_computed_from_durations(tmp_path):
    log_path = tmp_path / "log.jsonl"
    _write_log(log_path, [{"status": "verdict", "duration_seconds": float(i)} for i in range(1, 11)])
    summary = summarize_call_log(log_path=log_path)
    assert summary.avg_duration_seconds == 5.5
    assert summary.max_duration_seconds == 10.0
    assert summary.p50_duration_seconds is not None
    assert summary.p95_duration_seconds is not None
    # p95 should be higher than p50 for a monotonically increasing series
    assert summary.p95_duration_seconds >= summary.p50_duration_seconds


def test_calls_missing_duration_are_counted_but_not_averaged(tmp_path):
    log_path = tmp_path / "log.jsonl"
    _write_log(log_path, [
        {"status": "verdict", "duration_seconds": 2.0},
        {"status": "verdict"},  # older log line from before duration_seconds existed
    ])
    summary = summarize_call_log(log_path=log_path)
    assert summary.total_calls == 2
    assert summary.calls_missing_duration == 1
    assert summary.avg_duration_seconds == 2.0  # only the one real value counted


def test_limit_only_considers_most_recent_lines(tmp_path):
    log_path = tmp_path / "log.jsonl"
    _write_log(log_path, [{"status": "verdict", "duration_seconds": 1.0}] * 5
               + [{"status": "system_error", "duration_seconds": 1.0}] * 5)
    # Without a limit: 5/10 errors = 0.5
    assert summarize_call_log(log_path=log_path).error_rate == 0.5
    # Limited to the most recent 5 lines, which are all system_error:
    summary = summarize_call_log(log_path=log_path, limit=5)
    assert summary.total_calls == 5
    assert summary.error_rate == 1.0


def test_corrupted_line_is_skipped_not_fatal(tmp_path):
    log_path = tmp_path / "log.jsonl"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"status": "verdict", "duration_seconds": 1.0}) + "\n")
        f.write("{not valid json\n")
        f.write(json.dumps({"status": "verdict", "duration_seconds": 2.0}) + "\n")
    summary = summarize_call_log(log_path=log_path)
    assert summary.total_calls == 2
