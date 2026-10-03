import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "webapp" / "backend"))

import pytest
from app import app, _JOBS
from src.pipeline import PairResult
from src.progress import ProgressTracker


@pytest.fixture
def client():
    import app as app_module
    app.config["TESTING"] = True
    app.config["RATELIMIT_ENABLED"] = False
    # Flask-Limiter reads RATELIMIT_ENABLED from app.config once at
    # init_app time (limiter.enabled is set from config.setdefault), so a
    # post-import config toggle alone does not actually disable it -- the
    # enforcement flag lives on the Limiter instance. Toggle that directly
    # so the suite is not fragile to the number of requests it makes.
    app_module.limiter.enabled = False
    with app.test_client() as c:
        yield c


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_sample_returns_reference_list_and_sentences(client):
    resp = client.get("/api/sample")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "reference_list" in data
    assert "sentences" in data
    assert len(data["reference_list"]) >= 1
    assert len(data["sentences"]) >= 1


def test_analyze_rejects_empty_sentences(client):
    resp = client.post("/api/analyze", json={"reference_list": [{"number": 1, "doi": "10.1/x"}], "sentences": []})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_analyze_rejects_empty_reference_list(client):
    resp = client.post("/api/analyze", json={"reference_list": [], "sentences": [{"id": "s1", "text": "x [1]."}]})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_analyze_rejects_malformed_sentence(client):
    resp = client.post("/api/analyze", json={
        "reference_list": [{"number": 1, "doi": "10.1/x"}],
        "sentences": [{"id": "s1"}],  # missing "text"
    })
    assert resp.status_code == 400
    assert "missing" in resp.get_json()["error"]


def test_analyze_rejects_malformed_reference(client):
    resp = client.post("/api/analyze", json={
        "reference_list": [{"number": 1}],  # missing "doi"
        "sentences": [{"id": "s1", "text": "x [1]."}],
    })
    assert resp.status_code == 400
    assert "missing" in resp.get_json()["error"]


@patch("app.require_contact_email", return_value="test@example.com")
@patch("app.build_metadata_report", return_value={"bibliography_status": [], "document_level_findings": [], "not_evaluated_checks": []})
@patch("app.process_sentences")
def test_analyze_accepts_identifier_free_reference_with_search_keys(mock_process_sentences, mock_metadata, mock_email, client):
    mock_process_sentences.return_value = []

    resp = client.post("/api/analyze", json={
        "reference_list": [{"number": 1, "title": "No DOIs", "first_author": "Smith", "publication_year": 2020}],
        "sentences": [{"id": "s1", "text": "A claim [1]."}],
    })

    assert resp.status_code == 200
    call_args = mock_process_sentences.call_args
    assert call_args.args[1] == {1: {"number": 1, "title": "No DOIs", "first_author": "Smith", "publication_year": 2020}}


def test_analyze_rejects_non_numeric_reference_number(client):
    resp = client.post("/api/analyze", json={
        "reference_list": [{"number": "not-a-number", "doi": "10.1/x"}],
        "sentences": [{"id": "s1", "text": "x [1]."}],
    })
    assert resp.status_code == 400
    assert "non-numeric" in resp.get_json()["error"]


@patch("app.require_contact_email", return_value="test@example.com")
@patch("app.build_metadata_report", return_value={"bibliography_status": [], "document_level_findings": [], "not_evaluated_checks": []})
@patch("app.process_sentences")
def test_analyze_success(mock_process_sentences, mock_metadata, mock_email, client):
    mock_process_sentences.return_value = [
        PairResult(
            sentence_id="s1", sentence_text="A claim [1].", ref_number=1, doi="10.1/x",
            status="verdict", verdict="supported", source_type="full_text", citation_context="sole",
            llm_call_attempted=True,
        )
    ]

    resp = client.post("/api/analyze", json={
        "reference_list": [{"number": 1, "doi": "10.1/x"}],
        "sentences": [{"id": "s1", "text": "A claim [1]."}],
    })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["summary"]["total_sentences"] == 1
    assert data["summary"]["total_checks"] == 1
    assert data["sentences"][0]["verdict"] == "supported"

    # Confirm the reference_list was correctly passed to the pipeline, keeping
    # the entry dicts (number/doi) so identifier-free entries can route
    # through extended retrieval too.
    call_args = mock_process_sentences.call_args
    assert call_args.args[1] == {1: {"number": 1, "doi": "10.1/x"}}


# ---------------------------------------------------------------------------
# Async job flow: POST /api/analyze-text -> 202 + job_id, then poll
# /api/progress/<job_id> for live events and the final report.
# ---------------------------------------------------------------------------

def test_progress_tracker_records_events_and_snapshots_independently():
    tracker = ProgressTracker()
    tracker.emit("parse", None, "Reading your text…")
    tracker.emit("llm", 3, "Reference [3] — comparing")
    tracker.emit("verdict", 3, "Reference [3] — Supported")

    events = tracker.snapshot()
    assert [e["stage"] for e in events] == ["parse", "llm", "verdict"]
    assert events[1]["ref"] == 3
    assert "Supported" in events[2]["message"]

    # Snapshot is a copy; later emissions don't leak into it.
    tracker.emit("verdict", 4, "Reference [4] — Not supported")
    assert len(events) == 3
    assert len(tracker.snapshot()) == 4


@patch("app.require_contact_email", return_value="test@example.com")
@patch("app.build_metadata_report", return_value={"bibliography_status": [], "document_level_findings": [], "not_evaluated_checks": []})
@patch("app.process_sentences")
def test_analyze_text_returns_job_and_progress_reaches_done(mock_process_sentences, mock_metadata, mock_email, client):
    def fake_process(sentences, reference_list, contact_email, max_workers=6, progress=None):
        if progress is not None:
            progress.emit("llm", 1, "Reference [1] — comparing")
            progress.emit("verdict", 1, "Reference [1] — Supported")
        return [
            PairResult(
                sentence_id="s1", sentence_text="A claim [1].", ref_number=1, doi="10.1/x",
                status="verdict", verdict="supported", source_type="abstract", citation_context="sole",
                llm_call_attempted=True,
            )
        ]

    mock_process_sentences.side_effect = fake_process

    resp = client.post("/api/analyze-text", json={"text": "A claim [1].\n\nReferences\n[1] A, B. Title. 2024. https://doi.org/10.1/x"})
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["status"] == "running"
    job_id = data["job_id"]
    assert job_id
    assert data["sentence_count"] == 1

    # Poll until the background thread finishes.
    report = None
    for _ in range(50):
        p = client.get(f"/api/progress/{job_id}").get_json()
        assert p["status"] in ("running", "done", "error")
        if p["status"] == "running":
            assert isinstance(p["events"], list)
            time.sleep(0.02)
            continue
        assert p["status"] == "done", p.get("error")
        assert any("comparing" in e["message"] for e in p["events"])
        report = p["report"]
        break

    assert report is not None
    assert report["summary"]["total_sentences"] == 1
    assert report["sentences"][0]["verdict"] == "supported"

    # Confirm the tracker was threaded through to the pipeline.
    assert mock_process_sentences.call_args.kwargs.get("progress") is not None


def test_progress_unknown_job_returns_404(client):
    resp = client.get("/api/progress/does-not-exist")
    assert resp.status_code == 404


@pytest.fixture(autouse=True)
def _clean_jobs():
    # Keep the module-level job store from leaking across tests.
    before = set(_JOBS.keys())
    yield
    for key in set(_JOBS.keys()):
        if key not in before:
            del _JOBS[key]


# ---------------------------------------------------------------------------
# API key auth -- opt-in via env var, off by default (matches ALLOWED_ORIGIN).
# ---------------------------------------------------------------------------

def test_analyze_open_when_api_key_unset(client):
    import app as app_module
    assert app_module.API_KEY == ""  # default in test env
    resp = client.post("/api/analyze", json={"reference_list": [], "sentences": []})
    # Reaches the normal validation logic (400 for empty body) rather than
    # a 401 -- i.e. auth was not enforced.
    assert resp.status_code == 400


def test_analyze_requires_api_key_when_configured(client):
    import app as app_module
    with patch.object(app_module, "API_KEY", "secret-123"):
        resp = client.post("/api/analyze", json={"reference_list": [], "sentences": []})
        assert resp.status_code == 401

        resp_ok = client.post(
            "/api/analyze",
            json={"reference_list": [], "sentences": []},
            headers={"X-API-Key": "secret-123"},
        )
        # Wrong/missing key is blocked (401); the right key clears auth and
        # falls through to normal validation (400 for the empty payload).
        assert resp_ok.status_code == 400


def test_analyze_rejects_wrong_api_key(client):
    import app as app_module
    with patch.object(app_module, "API_KEY", "secret-123"):
        resp = client.post(
            "/api/analyze",
            json={"reference_list": [], "sentences": []},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Rate limiting -- always on; exercised directly since the `client` fixture
# disables enforcement for every other test.
# ---------------------------------------------------------------------------

def test_analyze_is_rate_limited_per_ip():
    import app as app_module
    app.config["TESTING"] = True
    app.config["RATELIMIT_ENABLED"] = True
    app_module.limiter.reset()  # isolate from any hits recorded by other tests
    app_module.limiter.enabled = True  # the client fixture disables this; re-enable for this test
    try:
        with app.test_client() as c:
            # RATE_LIMIT_ANALYZE defaults to "10 per hour" and is bound to the
            # route at decoration time, so exercise that real default rather
            # than trying to patch it post-import (the decorator won't see it).
            responses = [c.post("/api/analyze", json={"reference_list": [], "sentences": []}) for _ in range(11)]
            statuses = [r.status_code for r in responses]
        assert statuses[:10].count(429) == 0, f"first 10 requests should all clear the limiter, got {statuses[:10]}"
        assert statuses[10] == 429, f"11th request from the same IP should be throttled, got {statuses[10]}"
    finally:
        app_module.limiter.reset()
        app.config["RATELIMIT_ENABLED"] = False
        app_module.limiter.enabled = False


# ---------------------------------------------------------------------------
# Job store TTL -- stale jobs must not accumulate in memory forever.
# ---------------------------------------------------------------------------

def test_purge_stale_jobs_removes_only_expired_entries():
    import app as app_module

    with app_module._JOBS_LOCK:
        app_module._JOBS.clear()
        now = app_module.time.time()
        app_module._JOBS["old-job"] = {"status": "done", "created_at": now - app_module.JOB_TTL_SECONDS - 10}
        app_module._JOBS["fresh-job"] = {"status": "done", "created_at": now}
        app_module._purge_stale_jobs()
        remaining = set(app_module._JOBS.keys())

    assert remaining == {"fresh-job"}
    # keep other tests' cleanup fixture happy
    with app_module._JOBS_LOCK:
        app_module._JOBS.clear()


def test_created_at_is_not_exposed_in_progress_response(client):
    import app as app_module
    with app_module._JOBS_LOCK:
        app_module._JOBS["visible-job"] = {
            "status": "done", "events": [], "report": {}, "error": None,
            "created_at": app_module.time.time(),
        }

    resp = client.get("/api/progress/visible-job")
    assert resp.status_code == 200
    assert "created_at" not in resp.get_json()

    with app_module._JOBS_LOCK:
        del app_module._JOBS["visible-job"]


@patch("app.require_contact_email", return_value="test@example.com")
@patch("app.build_metadata_report", return_value={"bibliography_status": [], "document_level_findings": [], "not_evaluated_checks": []})
@patch("app.process_sentences")
def test_analyze_text_stamps_created_at_and_purges_stale_jobs(mock_process_sentences, mock_metadata, mock_email, client):
    import app as app_module
    mock_process_sentences.return_value = []

    with app_module._JOBS_LOCK:
        app_module._JOBS["ancient-job"] = {
            "status": "done",
            "created_at": app_module.time.time() - app_module.JOB_TTL_SECONDS - 1,
        }

    resp = client.post("/api/analyze-text", json={"text": "A claim [1].\n\nReferences\n[1] A, B. Title. 2024. https://doi.org/10.1/x"})
    assert resp.status_code == 202
    new_job_id = resp.get_json()["job_id"]

    with app_module._JOBS_LOCK:
        assert "ancient-job" not in app_module._JOBS, "stale job should have been purged on new job creation"
        assert new_job_id in app_module._JOBS
        assert isinstance(app_module._JOBS[new_job_id]["created_at"], float)


# ---------------------------------------------------------------------------
# /api/metrics -- basic observability over the LLM call log.
# ---------------------------------------------------------------------------

def test_metrics_open_when_api_key_unset(client):
    resp = client.get("/api/metrics")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "total_calls" in body
    assert "error_rate" in body
    assert "latency_seconds" in body


def test_metrics_requires_api_key_when_configured(client):
    import app as app_module
    with patch.object(app_module, "API_KEY", "secret-123"):
        resp = client.get("/api/metrics")
        assert resp.status_code == 401

        resp_ok = client.get("/api/metrics", headers={"X-API-Key": "secret-123"})
        assert resp_ok.status_code == 200


def test_metrics_rejects_non_integer_limit(client):
    resp = client.get("/api/metrics?limit=not-a-number")
    assert resp.status_code == 400


def test_metrics_endpoint_forwards_summary_into_response_shape(client):
    import app as app_module
    from src.metrics import CallLogSummary

    fake_summary = CallLogSummary(
        total_calls=7,
        error_count=2,
        error_rate=2 / 7,
        status_counts={"verdict": 5, "system_error": 2},
        avg_duration_seconds=1.23,
        p50_duration_seconds=1.0,
        p95_duration_seconds=2.5,
        max_duration_seconds=3.0,
        calls_missing_duration=1,
    )
    with patch.object(app_module, "summarize_call_log", return_value=fake_summary) as mock_summarize:
        resp = client.get("/api/metrics?limit=42")

    assert resp.status_code == 200
    mock_summarize.assert_called_once_with(limit=42)
    body = resp.get_json()
    assert body["total_calls"] == 7
    assert body["error_count"] == 2
    assert round(body["error_rate"], 4) == round(2 / 7, 4)
    assert body["status_counts"] == {"verdict": 5, "system_error": 2}
    assert body["latency_seconds"] == {"avg": 1.23, "p50": 1.0, "p95": 2.5, "max": 3.0}
    assert body["calls_missing_duration"] == 1


# ---------------------------------------------------------------------------
# Metadata module wiring -- bibliography_status / document_level_findings /
# not_evaluated_checks should appear in both /api/analyze and the
# /api/analyze-text async job's final report.
# ---------------------------------------------------------------------------

_FAKE_METADATA_REPORT = {
    "bibliography_status": [{
        "reference_id": 1, "status": "well_cited", "usable_identifier": "10.1/x",
        "findings": [], "used_in_sentences": ["s1"], "blocks_phase_2": False,
    }],
    "document_level_findings": [],
    "not_evaluated_checks": [{"check_id": 14, "component": "screening_criterion", "reason": "stub"}],
}


@patch("app.require_contact_email", return_value="test@example.com")
@patch("app.build_metadata_report", return_value=_FAKE_METADATA_REPORT)
@patch("app.process_sentences")
def test_analyze_merges_metadata_report_into_response(mock_process_sentences, mock_metadata, mock_email, client):
    mock_process_sentences.return_value = [
        PairResult(
            sentence_id="s1", sentence_text="A claim [1].", ref_number=1, doi="10.1/x",
            status="verdict", verdict="supported", source_type="full_text", citation_context="sole",
            llm_call_attempted=True,
        )
    ]

    resp = client.post("/api/analyze", json={
        "reference_list": [{"number": 1, "doi": "10.1/x"}],
        "sentences": [{"id": "s1", "text": "A claim [1]."}],
    })

    assert resp.status_code == 200
    data = resp.get_json()
    # Content results are untouched.
    assert data["sentences"][0]["verdict"] == "supported"
    # Metadata results are merged in alongside them.
    assert data["bibliography_status"] == _FAKE_METADATA_REPORT["bibliography_status"]
    assert data["document_level_findings"] == []
    assert any(c["check_id"] == 14 for c in data["not_evaluated_checks"])
    assert "metadata_error" not in data


@patch("app.require_contact_email", return_value="test@example.com")
@patch("app.build_metadata_report", side_effect=RuntimeError("Crossref is down"))
@patch("app.process_sentences")
def test_analyze_metadata_failure_does_not_break_content_response(mock_process_sentences, mock_metadata, mock_email, client):
    """
    Metadata is a secondary, additive axis -- if it blows up, the primary
    content-verdict response must still come through, with the failure
    surfaced rather than silently swallowed.
    """
    mock_process_sentences.return_value = [
        PairResult(
            sentence_id="s1", sentence_text="A claim [1].", ref_number=1, doi="10.1/x",
            status="verdict", verdict="supported", source_type="full_text", citation_context="sole",
            llm_call_attempted=True,
        )
    ]

    resp = client.post("/api/analyze", json={
        "reference_list": [{"number": 1, "doi": "10.1/x"}],
        "sentences": [{"id": "s1", "text": "A claim [1]."}],
    })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["sentences"][0]["verdict"] == "supported"  # content still came through
    assert data["bibliography_status"] == []
    assert "Crossref is down" in data["metadata_error"]


@patch("app.require_contact_email", return_value="test@example.com")
@patch("app.build_metadata_report", return_value=_FAKE_METADATA_REPORT)
@patch("app.process_sentences")
def test_analyze_text_job_report_includes_metadata(mock_process_sentences, mock_metadata, mock_email, client):
    mock_process_sentences.return_value = [
        PairResult(
            sentence_id="s1", sentence_text="A claim [1].", ref_number=1, doi="10.1/x",
            status="verdict", verdict="supported", source_type="full_text", citation_context="sole",
            llm_call_attempted=True,
        )
    ]

    resp = client.post("/api/analyze-text", json={"text": "A claim [1].\n\nReferences\n[1] A, B. Title. 2024. https://doi.org/10.1/x"})
    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]

    report = None
    for _ in range(50):
        p = client.get(f"/api/progress/{job_id}").get_json()
        if p["status"] == "running":
            time.sleep(0.02)
            continue
        assert p["status"] == "done", p.get("error")
        report = p["report"]
        break

    assert report is not None
    assert report["bibliography_status"] == _FAKE_METADATA_REPORT["bibliography_status"]
    assert any(c["check_id"] == 14 for c in report["not_evaluated_checks"])


@patch("app.require_contact_email", return_value="test@example.com")
@patch("app.build_declared_references")
@patch("app.build_metadata_report", return_value=_FAKE_METADATA_REPORT)
@patch("app.process_sentences")
def test_analyze_text_passes_raw_bibliography_text_to_metadata_module(
    mock_process_sentences, mock_metadata, mock_build_declared, mock_email, client
):
    """
    /api/analyze-text has the full original pasted text available (unlike
    /api/analyze's direct-JSON input) -- confirms that raw reference text
    actually reaches build_declared_references, not just a bare DOI.
    """
    mock_process_sentences.return_value = []
    mock_build_declared.return_value = []

    text = "A claim [1].\n\nReferences\n[1] Smith, J. (2020). A title. Journal, 1, 1-2. https://doi.org/10.1/x"
    resp = client.post("/api/analyze-text", json={"text": text})
    job_id = resp.get_json()["job_id"]

    for _ in range(50):
        p = client.get(f"/api/progress/{job_id}").get_json()
        if p["status"] == "running":
            time.sleep(0.02)
            continue
        break

    call_args = mock_build_declared.call_args
    reference_entries = call_args.args[0]
    assert reference_entries[0]["raw"].startswith("[1] Smith, J. (2020)")


# ---------------------------------------------------------------------------
# /api/download-log -- lets the raw LLM call log be pulled from a Render
# free-tier instance (no shell/persistent disk) via the browser.
# ---------------------------------------------------------------------------

def test_download_log_returns_404_when_no_log_file_exists(client, tmp_path):
    import app as app_module
    with patch.object(app_module, "DEFAULT_LOG_PATH", tmp_path / "does_not_exist.jsonl"):
        resp = client.get("/api/download-log")
    assert resp.status_code == 404


def test_download_log_returns_file_content_as_attachment(client, tmp_path):
    import app as app_module
    log_path = tmp_path / "log.jsonl"
    log_path.write_text('{"status": "verdict", "claim": "x", "excerpt": "y"}\n', encoding="utf-8")

    with patch.object(app_module, "DEFAULT_LOG_PATH", log_path):
        resp = client.get("/api/download-log")

    assert resp.status_code == 200
    assert b'"status": "verdict"' in resp.data
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    assert "llm_call_log.jsonl" in resp.headers.get("Content-Disposition", "")


def test_download_log_requires_api_key_when_configured(client, tmp_path):
    import app as app_module
    log_path = tmp_path / "log.jsonl"
    log_path.write_text('{"status": "verdict"}\n', encoding="utf-8")

    with patch.object(app_module, "API_KEY", "secret-123"), patch.object(app_module, "DEFAULT_LOG_PATH", log_path):
        resp = client.get("/api/download-log")
        assert resp.status_code == 401

        resp_ok = client.get("/api/download-log", headers={"X-API-Key": "secret-123"})
        assert resp_ok.status_code == 200


def test_download_log_open_when_api_key_unset(client, tmp_path):
    import app as app_module
    log_path = tmp_path / "log.jsonl"
    log_path.write_text('{"status": "verdict"}\n', encoding="utf-8")

    with patch.object(app_module, "DEFAULT_LOG_PATH", log_path):
        resp = client.get("/api/download-log")
    assert resp.status_code == 200
