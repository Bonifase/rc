import json

import app as app_module
import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "JOB_DIR", tmp_path)
    return app_module.app.test_client()


JOB_ID = "a" * 32


def test_unknown_job_is_a_clean_404_json(client):
    response = client.get("/api/progress/" + "b" * 32)

    assert response.status_code == 404
    assert response.get_json() == {"error": "Unknown job."}


def test_finished_job_survives_a_server_restart(client, tmp_path):
    (tmp_path / JOB_ID).mkdir()
    (tmp_path / JOB_ID / "report.json").write_text(json.dumps({"source_file": "m.pdf", "cases": []}), encoding="utf-8")
    (tmp_path / JOB_ID / "run_record.json").write_text(json.dumps({"publication_status": "draft"}), encoding="utf-8")

    payload = client.get("/api/progress/" + JOB_ID).get_json()

    assert payload["status"] == "done"
    assert payload["report"]["source_file"] == "m.pdf"
    assert payload["report"]["run_record"]["publication_status"] == "draft"


def test_job_interrupted_by_a_restart_is_reported_not_unknown(client, tmp_path):
    (tmp_path / JOB_ID).mkdir()
    (tmp_path / JOB_ID / "job.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")

    payload = client.get("/api/progress/" + JOB_ID).get_json()

    assert payload["status"] == "error"
    assert "restarted" in payload["error"]


def test_job_ids_that_are_not_hex_never_touch_the_filesystem(client):
    assert client.get("/api/progress/..%2F..%2Fetc").status_code == 404
    assert client.get("/api/progress/not-a-job-id").status_code == 404


def test_health_reports_configuration_without_leaking_secrets(client, monkeypatch):
    monkeypatch.setattr("src.config.OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setattr("src.config.CONTACT_EMAIL", "")
    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", "1")
    monkeypatch.setenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "0")
    monkeypatch.delenv("REFCHECK_DATA_DIR", raising=False)

    response = client.get("/api/health")

    assert response.get_json() == {
        "status": "ok", "openai_key_set": True, "contact_email_set": False,
        "live_judge": True, "offline_simulation": False, "persistent_storage_configured": False,
    }
    assert "sk-secret-value" not in response.get_data(as_text=True)


def _finished_job(tmp_path):
    job = tmp_path / JOB_ID
    job.mkdir()
    (job / "report.json").write_text(json.dumps({"source_file": "m.pdf"}), encoding="utf-8")
    (job / "run_record.json").write_text(json.dumps({"publication_status": "draft"}), encoding="utf-8")
    (job / "judge_log.jsonl").write_text(json.dumps({"call_key": "k1"}) + "\n", encoding="utf-8")
    return job


def test_every_job_file_is_downloadable_after_a_restart(client, tmp_path):
    _finished_job(tmp_path)
    app_module.UI_JOBS.clear()

    for name in ("report.json", "run_record.json", "judge_log.jsonl"):
        response = client.get(f"/api/download/{JOB_ID}/{name}")
        assert response.status_code == 200, name
        assert response.headers["Content-Disposition"].startswith("attachment")

    assert json.loads(client.get(f"/api/download/{JOB_ID}/run_record.json").data)["publication_status"] == "draft"
    assert client.get(f"/api/download/{JOB_ID}/judge_log.jsonl").data.decode().strip() == '{"call_key": "k1"}'


def test_downloads_are_limited_to_the_job_files(client, tmp_path):
    _finished_job(tmp_path)
    (tmp_path / JOB_ID / "secret.txt").write_text("x", encoding="utf-8")

    assert client.get(f"/api/download/{JOB_ID}/secret.txt").status_code == 404
    assert client.get(f"/api/download/{JOB_ID}/..%2Fsecret.txt").status_code == 404
    assert client.get("/api/download/not-a-job/report.json").status_code == 404
    assert client.get(f"/api/download/{'c' * 32}/report.json").status_code == 404


def test_a_failed_analysis_leaves_a_downloadable_run_record_and_a_clear_status(client, tmp_path, monkeypatch):
    def explode(source, out_dir):
        raise RuntimeError("OpenAI rejected the request for gpt-5.6-luna (HTTP 401)")

    monkeypatch.setattr(app_module.orchestrator_runner, "run_orchestrator", explode)
    source = tmp_path / "paper.docx"
    source.write_bytes(b"x")

    app_module._background_analysis(JOB_ID, source)
    app_module.UI_JOBS.clear()  # simulate the restart

    record = json.loads(client.get(f"/api/download/{JOB_ID}/run_record.json").data)
    assert record["status"] == "failed" and "HTTP 401" in record["error"] and "Traceback" in record["traceback"]
    progress = client.get(f"/api/progress/{JOB_ID}").get_json()
    assert progress["status"] == "error" and "HTTP 401" in progress["error"]


def test_the_model_call_ledger_is_downloadable(client):
    from src.model_call_store import cached_call

    assert client.get("/api/download-call-ledger").status_code == 404
    cached_call(model="m", system="s", user="u", slot="vote1", send=lambda: ("reply", {"prompt_tokens": 1, "completion_tokens": 1}))

    response = client.get("/api/download-call-ledger")

    assert response.status_code == 200
    assert json.loads(response.data.decode().splitlines()[0])["model"] == "m"
