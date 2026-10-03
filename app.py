from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from orchestrator.runner import run_orchestrator

ROOT_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT_DIR / "output" / "uploads"
JOB_DIR = ROOT_DIR / "output" / "jobs"

app = Flask(__name__)

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()



def _job_status(job_id: str) -> dict | None:
    with JOBS_LOCK:
        return JOBS.get(job_id)


def _set_job(job_id: str, *, status: str, progress: list[dict] | None = None, report: dict | None = None, error: str | None = None) -> None:
    with JOBS_LOCK:
        existing = JOBS.get(job_id, {})
        JOBS[job_id] = {
            "status": status,
            "progress": progress if progress is not None else existing.get("progress", []),
            "report": report if report is not None else existing.get("report"),
            "error": error if error is not None else existing.get("error"),
        }


def _background_analysis(job_id: str, source_path: Path) -> None:
    progress = [
        {"stage": "upload", "message": "Manuscript uploaded and queued."},
        {"stage": "parse", "message": "Extracting text and citations from the manuscript."},
    ]
    _set_job(job_id, status="running", progress=progress)

    try:
        out_dir = JOB_DIR / job_id
        result = run_orchestrator(source_path, out_dir)
        with open(result["report_json"], "r", encoding="utf-8") as fh:
            report = json.load(fh)
        run_record_path = Path(result["run_record"])
        if run_record_path.exists():
            with open(run_record_path, "r", encoding="utf-8") as fh:
                report["run_record"] = json.load(fh)
        report["output_files"] = {
            "report_json": "report.json",
            "excel_dataset": "dataset.xlsx",
            "run_record": "run_record.json",
        }
        progress.append({"stage": "done", "message": "RefCheck analysis complete."})
        _set_job(job_id, status="done", progress=progress, report=report)
    except Exception as exc:  # pragma: no cover - surfaced to the browser via API
        progress.append({"stage": "error", "message": f"Analysis failed: {exc}"})
        _set_job(job_id, status="error", progress=progress, error=str(exc))


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/upload-manuscript")
def upload_manuscript():
    if "file" not in request.files:
        return jsonify({"error": "No manuscript selected."}), 400

    uploaded = request.files["file"]
    if uploaded.filename == "":
        return jsonify({"error": "No manuscript selected."}), 400

    ext = Path(uploaded.filename).suffix.lower()
    if ext not in {".docx", ".pdf"}:
        return jsonify({"error": "Unsupported file type. Please upload a .docx or .pdf manuscript."}), 400

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    JOB_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = secure_filename(uploaded.filename)
    source_path = UPLOAD_DIR / safe_name
    uploaded.save(source_path)

    job_id = uuid.uuid4().hex
    _set_job(job_id, status="running", progress=[{"stage": "upload", "message": "Manuscript saved. Analysis queued."}])

    worker = threading.Thread(target=_background_analysis, args=(job_id, source_path), daemon=True)
    worker.start()

    return jsonify({"job_id": job_id, "status": "running"}), 202


@app.get("/api/progress/<job_id>")
def progress(job_id: str):
    job = _job_status(job_id)
    if job is None:
        return jsonify({"error": "Unknown job."}), 404

    payload: dict = {
        "status": job["status"],
        "progress": job.get("progress", []),
    }
    if job.get("error") is not None:
        payload["error"] = job["error"]
    if job.get("report") is not None:
        payload["report"] = job["report"]
    return jsonify(payload)


@app.get("/api/download/<job_id>/<path:filename>")
def download_file(job_id: str, filename: str):
    job = _job_status(job_id)
    if job is None:
        return jsonify({"error": "Unknown job."}), 404

    safe_name = Path(filename).name
    file_path = JOB_DIR / job_id / safe_name
    if not file_path.exists():
        return jsonify({"error": "File not found."}), 404
    # Ensure the file exists before attempting to send it

    return send_from_directory(str(JOB_DIR / job_id), safe_name, as_attachment=True, download_name=safe_name)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(debug=True, host="0.0.0.0", port=port, use_reloader=False)
