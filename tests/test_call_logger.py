import json

from src.call_logger import log_llm_call


def test_log_llm_call_writes_one_json_line(tmp_path):
    log_path = tmp_path / "test_log.jsonl"

    log_llm_call(
        claim="Cortisol impairs muscle growth.",
        excerpt="Cortisol administration increased proteolytic activity.",
        source_type="full_text",
        citation_context="sole",
        raw_response='{"verdict": "supported"}',
        status="verdict",
        log_path=log_path,
    )

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["claim"] == "Cortisol impairs muscle growth."
    assert record["excerpt"] == "Cortisol administration increased proteolytic activity."
    assert record["source_type"] == "full_text"
    assert record["citation_context"] == "sole"
    assert record["status"] == "verdict"
    assert "timestamp" in record


def test_log_llm_call_appends_multiple_calls(tmp_path):
    log_path = tmp_path / "test_log.jsonl"

    for i in range(3):
        log_llm_call(
            claim=f"claim {i}",
            excerpt="excerpt",
            source_type="abstract",
            citation_context="sole",
            raw_response=None,
            status="system_error",
            error_message="timeout",
            log_path=log_path,
        )

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3


def test_log_llm_call_records_duration_seconds(tmp_path):
    log_path = tmp_path / "test_log.jsonl"

    log_llm_call(
        claim="x", excerpt="y", source_type="full_text", citation_context="sole",
        raw_response='{"verdict": "supported"}', status="verdict",
        duration_seconds=1.234, log_path=log_path,
    )

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["duration_seconds"] == 1.234


def test_log_llm_call_duration_seconds_defaults_to_null(tmp_path):
    log_path = tmp_path / "test_log.jsonl"

    log_llm_call(
        claim="x", excerpt="y", source_type="full_text", citation_context="sole",
        raw_response=None, status="system_error", log_path=log_path,
    )

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["duration_seconds"] is None


def test_log_llm_call_does_not_raise_on_unwritable_path():
    # A directory that cannot be created (parent is a file, not a dir)
    # should be swallowed, not raised -- logging must never break the pipeline.
    from pathlib import Path
    bogus_path = Path("/dev/null/impossible/path.jsonl")
    log_llm_call(
        claim="x", excerpt="x", source_type="full_text", citation_context="sole",
        raw_response=None, status="verdict", log_path=bogus_path,
    )
    # No assertion needed -- test passes if no exception was raised.
