"""
Flask API wrapping the citation fidelity pipeline for the React frontend.
Talks directly to the existing src/ modules -- no duplicated logic between
the CLI (run.py) and this API.

Run with: python webapp/backend/app.py
"""
import hmac
import json
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Allow importing the project root's src/ package regardless of cwd.
ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from flask import (Flask, jsonify, render_template, request, send_file,
                   send_from_directory)
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename

from orchestrator import runner as orchestrator_runner
from src.aggregator import generate_report
from src.call_logger import DEFAULT_LOG_PATH
from src.config import require_contact_email
from src.metadata.document import MetadataDoc
from src.metadata.extraction import build_declared_references
from src.metadata.report import build_report as build_metadata_report
from src.metrics import summarize_call_log
from src.pipeline import process_sentences
from src.progress import ProgressTracker
from src.text_parser import parse_pasted_text

app = Flask(__name__, template_folder=str(ROOT_DIR / "templates"), static_folder=str(ROOT_DIR / "static"))

UPLOAD_DIR = ROOT_DIR / "output" / "uploads"
JOB_DIR = ROOT_DIR / "output" / "jobs"
UI_JOBS: dict[str, dict] = {}
UI_JOBS_LOCK = threading.Lock()


def _ui_job_status(job_id: str) -> dict | None:
    with UI_JOBS_LOCK:
        return UI_JOBS.get(job_id)


def _set_ui_job(job_id: str, *, status: str, progress: list[dict] | None = None, report: dict | None = None, error: str | None = None) -> None:
    with UI_JOBS_LOCK:
        existing = UI_JOBS.get(job_id, {})
        UI_JOBS[job_id] = {
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
    _set_ui_job(job_id, status="running", progress=progress)

    try:
        out_dir = JOB_DIR / job_id
        result = orchestrator_runner.run_orchestrator(source_path, out_dir)
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
        _set_ui_job(job_id, status="done", progress=progress, report=report)
    except Exception as exc:  # pragma: no cover - surfaced to the browser via API
        progress.append({"stage": "error", "message": f"Analysis failed: {exc}"})
        _set_ui_job(job_id, status="error", progress=progress, error=str(exc))

# Locked down via env var in production (e.g. your Vercel frontend URL).
# Supports a single origin or a comma-separated list:
#   ALLOWED_ORIGIN=https://fidelity-kappa.vercel.app
#   ALLOWED_ORIGIN=https://a.vercel.app,https://b.vercel.app
# Defaults to allow-all for local dev, where there's nothing sensitive to protect.
import os
import re

ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*").strip()
if not ALLOWED_ORIGIN or ALLOWED_ORIGIN == "*":
    CORS(app, origins="*")
else:
    # The browser sends the Origin header without a trailing slash, while the
    # configured value often picks one up by accident. Normalize so the two
    # always match, and match case-insensitively on the host.
    origins = [o.strip() for o in ALLOWED_ORIGIN.split(",") if o.strip()]
    patterns = []
    for o in origins:
        normalized = o.rstrip("/")
        parts = normalized.split("://", 1)
        scheme = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else normalized
        host, _, port = rest.lower().partition(":")
        pattern = re.compile(re.escape(f"{scheme}://{host}") + (re.escape(":" + port) if port else "") + r"/?")
        patterns.append(pattern)
    CORS(app, origins=patterns)

REQUIRED_SENTENCE_KEYS = {"id", "text"}
REQUIRED_REFERENCE_KEYS = {"number"}

# Every /api/analyze* call fans out into Crossref + NCBI + OpenAI calls per
# citation, so an unauthenticated, unthrottled endpoint is a real cost/abuse
# surface once this is deployed at a public URL. Both protections below are
# opt-in via env var so local dev stays frictionless (matches the pattern
# already used for ALLOWED_ORIGIN: secure when configured, permissive
# otherwise) -- but you should set API_KEY before sharing the deployed URL
# outside your own team.
#
#   API_KEY=some-long-random-string
#
# When set, the three endpoints below require a matching `X-API-Key` header;
# requests without it get a 401. When unset, auth is skipped entirely (as
# today). NOTE: if you turn this on, the React frontend needs to send that
# header too -- ask before enabling in a shared deployment, since the
# frontend doesn't do this yet.
API_KEY = os.getenv("API_KEY", "").strip()

# Rate limits are always on (they don't require any client-side change,
# unlike the API key). Defaults are conservative since each request can
# trigger many downstream paid calls; override via env if needed.
RATE_LIMIT_ANALYZE = os.getenv("RATE_LIMIT_ANALYZE", "10 per hour")
RATE_LIMIT_RECHECK = os.getenv("RATE_LIMIT_RECHECK", "30 per hour")

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],  # no blanket limit; applied per-route below
    storage_uri="memory://",  # fine for the single-gunicorn-worker deployment in render.yaml
)


def _require_api_key():
    """Returns a (response, status) tuple to abort with, or None to continue."""
    if not API_KEY:
        return None
    provided = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(provided, API_KEY):
        return jsonify({"error": "Missing or invalid API key."}), 401
    return None

# In-memory job store for the async /api/analyze-text flow. Safe as long as
# gunicorn runs a single worker (the default), which is all the free tier
# needs. Keyed by job id; holds live progress events plus the final report.
#
# Jobs never got removed here, so a long-running process would leak memory
# forever -- every analysis, however old, stayed in the dict. JOB_TTL_SECONDS
# bounds that: each job carries the time it was created, and _purge_stale_jobs
# drops anything older than the TTL. Purging happens opportunistically when a
# new job is created (cheap, and guarantees the dict can't grow unbounded
# relative to how often jobs actually get created) rather than running a
# separate sweep thread, which would be one more thing to manage for a
# single-worker deployment this size.
import time

JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", str(60 * 60)))  # 1 hour
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _purge_stale_jobs() -> None:
    """Must be called with _JOBS_LOCK held."""
    cutoff = time.time() - JOB_TTL_SECONDS
    stale = [job_id for job_id, job in _JOBS.items() if job.get("created_at", cutoff) < cutoff]
    for job_id in stale:
        del _JOBS[job_id]


def _run_content_and_metadata(
    sentences: list[dict],
    reference_list: dict[int, str],
    reference_entries: list[dict],
    body_text: str,
    contact_email: str,
    progress: ProgressTracker = None,
) -> tuple[list, dict]:
    """
    Runs the content-comparison pipeline (process_sentences) and the
    metadata module (build_metadata_report) concurrently -- they're fully
    independent (per the metadata module's own design intent: "no
    dependency on v16 or the content pipeline"), both spend most of their
    time waiting on external HTTP calls, so running them on two threads
    instead of sequentially cuts real wall-clock time rather than just
    being tidy.

    Returns (pair_results, metadata_report). If the metadata module raises
    for any reason, that's captured into metadata_report's own "error" key
    rather than failing the whole request -- content verdicts are the
    primary result and should still reach the user even if the metadata
    module (a secondary, additive axis) hit a problem.
    """
    declared_references = build_declared_references(reference_entries)
    doc = MetadataDoc(sentences=sentences, body_text=body_text)

    with ThreadPoolExecutor(max_workers=2) as pool:
        content_future = pool.submit(process_sentences, sentences, reference_list, contact_email, progress=progress)
        metadata_future = pool.submit(build_metadata_report, doc, declared_references, contact_email=contact_email)

        pair_results = content_future.result()  # let a content-pipeline failure raise, as before

        try:
            metadata_report = metadata_future.result()
        except Exception as exc:  # noqa: BLE001 -- metadata is additive; don't take down content results
            metadata_report = {
                "bibliography_status": [],
                "document_level_findings": [],
                "not_evaluated_checks": [],
                "error": f"Metadata module failed: {exc}",
            }

    return pair_results, metadata_report


def _start_analysis_job(
    job_id: str,
    sentences,
    reference_list,
    reference_entries,
    body_text,
    contact_email,
    tracker: ProgressTracker,
    parse_warnings,
) -> None:
    """Runs the pipeline in a background thread and stores the outcome."""
    try:
        pair_results, metadata_report = _run_content_and_metadata(
            sentences, reference_list, reference_entries, body_text, contact_email, progress=tracker
        )
        report = generate_report(pair_results)
        report["lang"] = "en"
        report["report_language"] = "en"
        report["parse_warnings"] = parse_warnings
        report["bibliography_status"] = metadata_report["bibliography_status"]
        report["document_level_findings"] = metadata_report["document_level_findings"]
        report["not_evaluated_checks"] = metadata_report["not_evaluated_checks"]
        report["official_records"] = metadata_report.get("official_records", {})
        if "error" in metadata_report:
            report["metadata_error"] = metadata_report["error"]
        with _JOBS_LOCK:
            created_at = _JOBS.get(job_id, {}).get("created_at", time.time())
            _JOBS[job_id] = {
                "status": "done",
                "events": tracker.snapshot(),
                "report": report,
                "error": None,
                "created_at": created_at,
            }
    except Exception as exc:  # noqa: BLE001 -- surface any failure via the job
        with _JOBS_LOCK:
            created_at = _JOBS.get(job_id, {}).get("created_at", time.time())
            _JOBS[job_id] = {
                "status": "error",
                "events": tracker.snapshot(),
                "report": None,
                "error": str(exc),
                "created_at": created_at,
            }


@app.route("/", methods=["GET"])
def root():
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
    _set_ui_job(job_id, status="running", progress=[{"stage": "upload", "message": "Manuscript saved. Analysis queued."}])

    worker = threading.Thread(target=_background_analysis, args=(job_id, source_path), daemon=True)
    worker.start()

    return jsonify({"job_id": job_id, "status": "running"}), 202


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/metrics", methods=["GET"])
def metrics():
    """
    Summarizes the LLM call log: total calls, error rate, status breakdown,
    and latency (avg / p50 / p95 / max) over the most recent calls. Exists
    so a run's health can be checked at a glance -- e.g. after a deploy, or
    if something feels slow -- instead of reading output/llm_call_log.jsonl
    by hand.

    Query params:
      limit (optional, default 500): how many of the most recent log lines
      to summarize. Pass a smaller number for a quick recent-health check,
      or a large one for a fuller picture.

    Behind the same auth as the analyze endpoints (when API_KEY is set),
    since call-level claim/excerpt text is diagnostic detail, not something
    that needs to be public.
    """
    auth_error = _require_api_key()
    if auth_error is not None:
        return auth_error

    try:
        limit = int(request.args.get("limit", 500))
    except ValueError:
        return jsonify({"error": "limit must be an integer."}), 400

    summary = summarize_call_log(limit=limit)
    return jsonify(
        {
            "total_calls": summary.total_calls,
            "error_count": summary.error_count,
            "error_rate": round(summary.error_rate, 4),
            "status_counts": summary.status_counts,
            "latency_seconds": {
                "avg": summary.avg_duration_seconds,
                "p50": summary.p50_duration_seconds,
                "p95": summary.p95_duration_seconds,
                "max": summary.max_duration_seconds,
            },
            "calls_missing_duration": summary.calls_missing_duration,
        }
    )


@app.route("/api/download-log", methods=["GET"])
def download_log():
    """
    Serves output/llm_call_log.jsonl as a direct file download.

    Exists specifically for Render's free tier: there's no persistent disk
    and no built-in file browser, so the only other way to get this file
    off a running instance is the Shell tab's `cat`-and-copy-paste, which
    is painful for anything but a trivially short log. This route makes
    "reproduce the bug, then click download" possible from the browser.

    Same caveat as Render's ephemeral filesystem in general: the log (and
    this route) only has whatever's accumulated since the instance's last
    restart/redeploy/spin-down -- download it before that happens, not
    after.

    Behind the same auth as /api/metrics (when API_KEY is set) -- this file
    contains every claim and excerpt sent to the LLM, which is diagnostic
    detail, not something that needs to be publicly downloadable.
    """
    auth_error = _require_api_key()
    if auth_error is not None:
        return auth_error

    if not DEFAULT_LOG_PATH.exists():
        return jsonify({"error": "No log file yet -- run an analysis first."}), 404

    return send_file(
        DEFAULT_LOG_PATH,
        mimetype="application/x-ndjson",
        as_attachment=True,
        download_name="llm_call_log.jsonl",
    )


@app.route("/api/sample", methods=["GET"])
def sample():
    """Returns the sample reference list + sentences, for a 'Load sample' button."""
    sample_path = ROOT_DIR / "data" / "sample_input.json"
    with open(sample_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)


@app.route("/api/analyze", methods=["POST"])
@limiter.limit(RATE_LIMIT_ANALYZE)
def analyze():
    """
    Body: {
      "reference_list": [{"number": 1, "doi": "10.xxxx/..."}, ...],
      "sentences": [{"id": "s1", "text": "A claim citing a source [1]."}, ...]
    }
    Returns the same report shape as run.py's --output JSON, plus the
    metadata module's output merged in: "bibliography_status" (one entry
    per reference), "document_level_findings" (checks not tied to a single
    reference), and "not_evaluated_checks". Since this endpoint's input has
    no raw bibliography text (just number+DOI pairs), metadata checks that
    need declared fields like journal/volume/authors will have less to
    compare against than /api/analyze-text's fuller pipeline -- resolution
    still runs off the DOI alone.
    """
    auth_error = _require_api_key()
    if auth_error is not None:
        return auth_error

    payload = request.get_json(silent=True) or {}
    reference_entries = payload.get("reference_list", [])
    sentences = payload.get("sentences", [])

    if not sentences:
        return jsonify({"error": "No sentences provided."}), 400
    if not reference_entries:
        return jsonify({"error": "No reference list provided."}), 400

    reference_list = {}
    for i, entry in enumerate(reference_entries):
        missing = REQUIRED_REFERENCE_KEYS - entry.keys()
        if missing:
            return jsonify({"error": f"reference_list entry at index {i} missing: {sorted(missing)}"}), 400
        try:
            int(entry["number"])
        except (TypeError, ValueError):
            return jsonify({"error": f"reference_list entry at index {i} has a non-numeric 'number'."}), 400
        searchable = entry.get("doi") or entry.get("raw") or (entry.get("title") and entry.get("publication_year"))
        if not searchable:
            return jsonify({
                "error": (
                    f"reference_list entry at index {i} is missing a doi (and carries no title + "
                    "publication_year or raw bibliography text to search by)."
                )
            }), 400
        reference_list[int(entry["number"])] = entry

    for i, sentence in enumerate(sentences):
        missing = REQUIRED_SENTENCE_KEYS - sentence.keys()
        if missing:
            return jsonify({"error": f"sentences entry at index {i} missing: {sorted(missing)}"}), 400

    try:
        contact_email = require_contact_email()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    pair_results, metadata_report = _run_content_and_metadata(
        sentences, reference_list, reference_entries, "", contact_email
    )
    report = generate_report(pair_results)
    report["lang"] = "en"
    report["report_language"] = "en"
    report["bibliography_status"] = metadata_report["bibliography_status"]
    report["document_level_findings"] = metadata_report["document_level_findings"]
    report["not_evaluated_checks"] = metadata_report["not_evaluated_checks"]
    report["official_records"] = metadata_report.get("official_records", {})
    if "error" in metadata_report:
        report["metadata_error"] = metadata_report["error"]
    return jsonify(report)


@app.route("/api/parse-text", methods=["POST"])
def parse_text():
    """
    Accepts raw pasted text, parses out references and sentences.
    Body: { "text": "..." }
    Returns: {
      "reference_list": [...],
      "sentences": [...],
      "warnings": [...],
      "word_count": int
    }
    No LLM calls -- just parsing.
    """
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")

    if not text.strip():
        return jsonify({"error": "No text provided."}), 400

    result = parse_pasted_text(text)
    word_count = len(text.split())

    return jsonify({
        "reference_list": result.reference_list,
        "sentences": result.sentences,
        "warnings": result.warnings,
        "word_count": word_count,
        "reference_count": len(result.reference_list),
        "sentence_count": len(result.sentences),
    })


@app.route("/api/analyze-text", methods=["POST"])
@limiter.limit(RATE_LIMIT_ANALYZE)
def analyze_text():
    """
    All-in-one endpoint: accepts raw pasted text, parses it, and starts the
    pipeline in a background thread. Returns 202 immediately with a job id;
    the frontend then polls /api/progress/<job_id> for live events and the
    final report.

    Body: { "text": "..." }

    A full manuscript takes 1-2 minutes (each citation runs Crossref -> NCBI
    -> content fetch -> an LLM call), so returning after all of that would
    leave the browser staring at a spinner the whole time. Making the job
    async lets the UI show what the pipeline is doing.
    """
    auth_error = _require_api_key()
    if auth_error is not None:
        return auth_error

    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")

    if not text.strip():
        return jsonify({"error": "No text provided."}), 400

    # Parse the text
    parsed = parse_pasted_text(text)

    if not parsed.reference_list:
        return jsonify({
            "error": "No references could be found. Make sure your reference list includes DOI links "
            "(e.g. https://doi.org/10.xxxx/...) so each entry can be located by identifier.",
            "parse_warnings": parsed.warnings,
        }), 400

    if not parsed.sentences:
        return jsonify({
            "error": "No sentences with citation markers ([N]) were found in your text. Make sure your sentences contain bracketed reference numbers like [1], [3][8], or [4-6].",
            "parse_warnings": parsed.warnings,
        }), 400

    try:
        contact_email = require_contact_email()
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "parse_warnings": parsed.warnings}), 500

    # Build the reference_list dict the pipeline expects. Identifier-free
    # entries are passed as their raw dict so the extended cascade can search
    # by title + first author + year.
    reference_list = {}
    for entry in parsed.reference_list:
        reference_list[int(entry["number"])] = entry

    job_id = uuid.uuid4().hex
    tracker = ProgressTracker()
    tracker.emit("parse", None, "Reading your text and reference list…")
    tracker.emit(
        "parse",
        None,
        f"Found {len(parsed.reference_list)} references and {len(parsed.sentences)} cited sentences",
    )

    with _JOBS_LOCK:
        _purge_stale_jobs()
        _JOBS[job_id] = {
            "status": "running",
            "events": tracker.snapshot(),
            "report": None,
            "error": None,
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=_start_analysis_job,
        args=(job_id, parsed.sentences, reference_list, parsed.reference_list, text, contact_email, tracker, parsed.warnings),
        daemon=True,
    )
    thread.start()

    return jsonify({
        "job_id": job_id,
        "status": "running",
        "parse_warnings": parsed.warnings,
        "reference_count": len(parsed.reference_list),
        "sentence_count": len(parsed.sentences),
    }), 202


@app.route("/api/recheck", methods=["POST"])
@limiter.limit(RATE_LIMIT_RECHECK)
def recheck():
    """
    Re-runs the pipeline for a single sentence when a user wants to recheck a
    source after an error or doubt. Body: { "text": "the ORIGINAL pasted text",
    "sentence_id": "s3" }. The full text is re-parsed so the reference list
    stays consistent with the original run; only the one sentence is checked
    again. Returns the same report shape as /api/analyze-text (one sentence).

    Deliberately does NOT touch the metadata module. Metadata findings are
    reference-level, not sentence-level (a reference's citation metadata
    doesn't change because one sentence got rechecked), and check 8 needs
    every reference resolved together to do its cross-reference comparison
    -- recomputing it for a single sentence's reference(s) in isolation
    wouldn't be a meaningful result. The frontend should keep showing the
    bibliography_status from the original full analysis unchanged.
    """
    auth_error = _require_api_key()
    if auth_error is not None:
        return auth_error

    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")
    sentence_id = payload.get("sentence_id", "")

    if not text.strip():
        return jsonify({"error": "No text provided."}), 400
    if not sentence_id:
        return jsonify({"error": "No sentence_id provided."}), 400

    parsed = parse_pasted_text(text)

    if not parsed.reference_list:
        return jsonify({"error": "No references could be found. Make sure your reference list includes DOI links (e.g. https://doi.org/10.xxxx/...)."}), 400

    sentence = next((s for s in parsed.sentences if s["id"] == sentence_id), None)
    if sentence is None:
        return jsonify({
            "error": "The sentence could not be found after re-parsing the text.",
            "sentence_id": sentence_id,
        }), 404

    reference_list = {}
    for entry in parsed.reference_list:
        reference_list[int(entry["number"])] = entry

    try:
        contact_email = require_contact_email()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    pair_results = process_sentences([sentence], reference_list, contact_email)
    report = generate_report(pair_results)
    report["parse_warnings"] = parsed.warnings
    return jsonify(report)


@app.route("/api/progress/<job_id>", methods=["GET"])
def progress(job_id):
    """Return either the async text-analysis job progress or the file-upload job progress."""
    with UI_JOBS_LOCK:
        ui_job = UI_JOBS.get(job_id)
    if ui_job is not None:
        payload = {
            "status": ui_job["status"],
            "progress": ui_job.get("progress", []),
        }
        if ui_job.get("error") is not None:
            payload["error"] = ui_job["error"]
        if ui_job.get("report") is not None:
            payload["report"] = ui_job["report"]
        return jsonify(payload)

    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "Unknown job."}), 404
    public_job = {k: v for k, v in job.items() if k != "created_at"}
    return jsonify(public_job)


@app.get("/api/download/<job_id>/<path:filename>")
def download_file(job_id: str, filename: str):
    with UI_JOBS_LOCK:
        ui_job = UI_JOBS.get(job_id)
    if ui_job is not None:
        safe_name = Path(filename).name
        file_path = JOB_DIR / job_id / safe_name
        if not file_path.exists():
            return jsonify({"error": "File not found."}), 404
        return send_from_directory(str(JOB_DIR / job_id), safe_name, as_attachment=True, download_name=safe_name)

    return jsonify({"error": "Unknown job."}), 404


# ---------------------------------------------------------------------------
# /api/v2/runs: lightweight run ledger for the orchestration-style workflow
# ---------------------------------------------------------------------------
_V2_RUNS: dict[str, dict] = {}
_V2_RUNS_LOCK = threading.Lock()


def _v2_run_response(run: dict) -> dict:
    public = {k: v for k, v in run.items() if k not in {"created_at", "updated_at"}}
    public["run_id"] = public.get("run_id") or run.get("id")
    return public


def _v2_execute_run(run_id: str, payload: dict) -> None:
    try:
        source_path = payload.get("source_path") or payload.get("source_file")
        if not source_path:
            raise ValueError("source_path or source_file is required for a V2 run.")

        source_path = str(Path(source_path))
        if not Path(source_path).exists():
            raise FileNotFoundError(f"Source file does not exist: {source_path}")

        result = orchestrator_runner.run_orchestrator(source_path, payload.get("output_dir"))
        with _V2_RUNS_LOCK:
            run = _V2_RUNS.get(run_id)
            if run is None:
                return
            run["status"] = "completed"
            run["result"] = result
            run["updated_at"] = time.time()
    except Exception as exc:  # noqa: BLE001 -- route-level failures are stored in the run record
        with _V2_RUNS_LOCK:
            run = _V2_RUNS.get(run_id)
            if run is None:
                return
            run["status"] = "failed"
            run["error"] = str(exc)
            run["updated_at"] = time.time()


@app.route("/api/v2/runs", methods=["POST"])
def create_v2_run():
    """Create a run with a source path or exported manuscript file and execute it in process."""
    auth_error = _require_api_key()
    if auth_error is not None:
        return auth_error

    payload = request.get_json(silent=True) or {}
    source_path = payload.get("source_path") or payload.get("source_file")
    if not source_path:
        return jsonify({"error": "source_path or source_file is required."}), 400

    run_id = payload.get("run_id") or uuid.uuid4().hex
    now = time.time()
    run_record = {
        "run_id": run_id,
        "status": "queued",
        "source_path": str(source_path),
        "source_file": payload.get("source_file"),
        "created_at": now,
        "updated_at": now,
        "error": None,
        "result": None,
        "metadata": payload.get("metadata") or {},
    }

    with _V2_RUNS_LOCK:
        _V2_RUNS[run_id] = run_record

    thread = threading.Thread(target=_v2_execute_run, args=(run_id, payload), daemon=True)
    thread.start()

    return jsonify({"run": _v2_run_response(run_record), "status": "queued"}), 202


@app.route("/api/v2/runs", methods=["GET"])
def list_v2_runs():
    """List all V2 runs in the current process."""
    auth_error = _require_api_key()
    if auth_error is not None:
        return auth_error

    with _V2_RUNS_LOCK:
        runs = [_v2_run_response(run) for run in _V2_RUNS.values()]
    runs.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    return jsonify({"runs": runs, "count": len(runs)})


@app.route("/api/v2/runs/<run_id>", methods=["GET"])
def get_v2_run(run_id):
    """Fetch a single V2 run record."""
    auth_error = _require_api_key()
    if auth_error is not None:
        return auth_error

    with _V2_RUNS_LOCK:
        run = _V2_RUNS.get(run_id)
        if run is None:
            return jsonify({"error": "Run not found."}), 404
        return jsonify({"run": _v2_run_response(run)})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
