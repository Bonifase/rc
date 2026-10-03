# Citation Fidelity Checker

Checks whether a claim made in academic writing actually matches what its
cited source says. Paste a paper's text (or a pre-formatted JSON set of
claims + references), and the pipeline resolves each citation, fetches the
source's real content, and asks an LLM to verify the claim against that
content — returning a structured, evidence-quoted verdict instead of a
human having to check each citation by hand.

This README is a complete, step-by-step guide to running this app locally
and hosting your own deployment from scratch. Every environment variable,
command, and deploy step below reflects the code currently in this repo.

---

## Contents

1. [How it works](#how-it-works)
2. [Repo structure](#repo-structure)
3. [Prerequisites](#prerequisites)
4. [Local setup — backend](#local-setup--backend)
5. [Local setup — frontend](#local-setup--frontend)
6. [Smoke test: confirm everything works locally](#smoke-test-confirm-everything-works-locally)
7. [Deploying — step by step](#deploying--step-by-step)
8. [Environment variables reference](#environment-variables-reference)
9. [API reference](#api-reference)
10. [Testing](#testing)
11. [Observability](#observability)
12. [Troubleshooting](#troubleshooting)
13. [Design decisions worth knowing about](#design-decisions-worth-knowing-about)
14. [Known limitations / open items](#known-limitations--open-items)

---

## How it works

For each sentence with a citation marker (`[1]`, `[2, 3]`, etc.):

| Stage | File | What it does |
|---|---|---|
| 0 | `src/text_parser.py` | Parses pasted paper text into a reference list (number → DOI) + sentences with citation markers |
| 1 | `src/citation_parser.py` | Extracts which reference numbers a given sentence cites |
| 2 | `src/crossref.py` | Resolves a DOI via Crossref, returns metadata |
| 3 | `src/pmc_id_converter.py` | Converts a DOI to PMID/PMCID via NCBI |
| 4 | `src/content_fetcher.py` | Fetches PMC full text, falling back to a PubMed abstract |
| 5 | `src/llm_wrapper.py` | Sends claim + source content to OpenAI, returns a structured, schema-enforced verdict |
| 6 | `src/aggregator.py` | Builds the per-claim and document-level report |
| 7 | `run.py`, `webapp/backend/app.py`, `webapp/frontend/` | CLI and browser interfaces on top of the same pipeline |

`src/pipeline.py` orchestrates stages 2–5 per (sentence, citation) pair and
decides how each stage's outcome — success, "not found," "not indexed,"
transient error, etc. — maps to a final verdict label. A DOI cited by
multiple sentences is only resolved/fetched once per run (`SourceCache` in
`pipeline.py`), not once per sentence.

The LLM call itself is guarded three ways: it must return a response
matching a strict JSON schema (`response_format` in `llm_wrapper.py`), its
`evidence_quote` must be a real verbatim substring of the fetched source
text (checked in code, not trusted from the model), and the primary
temperature-0 call uses a fixed seed for best-effort determinism across
repeated runs on the same input.

### The metadata module (`src/metadata/`) — a second, independent axis

Everything above checks whether a *claim* matches its source (content
axis). `src/metadata/` checks whether a *reference itself* is correctly
cited — right journal, right authors, DOI pointing at the real paper —
mostly with no LLM and no per-token cost. A verdict attaches to a claim; a
metadata problem attaches to a reference. A sentence can be `Supported`
while its reference is `miscited` at the same time — not a contradiction,
the two axes are deliberately independent.

| Stage | File | What it does |
|---|---|---|
| 3a | `src/metadata/extraction.py` | Raw bibliography entry → structured `DeclaredReference` (authors, year, title, journal, volume, issue, pages, DOI) |
| — | `src/metadata/crossref_metadata.py` | Fetches the real record by DOI, or by title+author search; samples a journal's volume/year range by ISSN |
| 3b | `src/metadata/resolution.py` | Per-reference resolution flow: DOI → title+author fallback → rescue-on-wrong-article, exactly per spec |
| 3b | `src/metadata/checks/phase_a.py` | Checks 1 (repurposed), 2, 3, 4, 5, 13, 14 (rule-based half) — zero API calls |
| 3b | `src/metadata/checks/phase_b.py` | Checks 7, 9, 10, 11 — reuse the record already fetched during resolution |
| 3b | `src/metadata/checks/phase_c.py` | Check 8 (displaced authorship, O(n²)) + check 14's author-omission half — runs after every reference is resolved |
| 3c | `src/metadata/report.py` | Orchestrates all of the above into the report's third section: one status entry per reference, findings attached, never duplicated per citing sentence |

This module runs code-independently of the content pipeline above — no
shared HTTP session, no shared retry decorator, its own `MAX_RETRIES`-style
logic, matching the spec's "no dependency on v16 or the content pipeline."
It IS wired into both entry points (`run.py` and `webapp/backend/app.py`),
via `src/analysis_runner.py`, which runs the two independent pipelines
**concurrently** on two threads rather than sequentially — both spend most
of their time waiting on external HTTP calls, so this measurably cuts
wall-clock latency rather than just being tidy. If the metadata module
fails for any reason, that failure is captured into the report's
`metadata_error` field rather than taking down the primary content
verdicts, since metadata is a secondary, additive axis.

The webapp's report JSON (and the React UI's new "Reference citation
check" panel, below the sentence verdicts) carries three additional
top-level fields: `bibliography_status` (one entry per reference),
`document_level_findings` (checks not tied to a single reference, e.g. an
orphaned citation marker), and `not_evaluated_checks` (the un-implemented
semantic half of check 14, surfaced explicitly rather than silently
missing). `/api/recheck` deliberately does not touch metadata — it's a
whole-bibliography concept, not a per-sentence one; rechecking one
sentence's content verdict doesn't change any reference's citation
metadata.

See `MVP1_METADATA_MODULE_NOTES.md` for the two build-time decisions made
where the spec had gaps, and one real precision issue found through
testing (check 8's false-positive rate on bibliographies citing an
overlapping research group) that's worth validating before trusting its
output at face value.

---

## Repo structure

```
.
├── src/                      # The pipeline — see table above
│   └── metadata/              # MVP1: reference/citation-metadata module (second, independent axis)
├── run.py                    # CLI entry point (reads pre-formatted JSON)
├── webapp/
│   ├── backend/app.py        # Flask API wrapping src/
│   └── frontend/             # React (Vite) UI
├── data/sample_input.json    # Example input for the CLI / "Load sample" button
├── output/llm_call_log.jsonl # Append-only log of every LLM call (created on first run)
├── tests/                    # 252 tests, all against mocked HTTP/OpenAI — no live calls
├── guard_check.py            # Standalone script that re-verifies quote-guard behavior against a real run's output
├── MVP1_METADATA_MODULE_NOTES.md  # Build decisions + a precision issue found in src/metadata/ -- read before trusting check 8's output
├── render.yaml                # Render deploy config for the backend
└── .env.example               # Copy to .env and fill in (backend)
    webapp/frontend/.env.example  # Copy to .env (frontend)
```

---

## Prerequisites

- Python 3.10+
- Node.js 18+ and npm
- An OpenAI API key ([platform.openai.com](https://platform.openai.com))
- A real email address to identify yourself to Crossref/NCBI (see below — this is required, not optional)

---

## Local setup — backend

```bash
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and fill in at minimum:

- **`CONTACT_EMAIL`** — a real address. Crossref and NCBI both rate-limit
  unidentified traffic more aggressively; this is what gets you into their
  "polite pool." The app will refuse to run without it (`require_contact_email()`
  raises a clear error rather than silently sending anonymous traffic).
- **`OPENAI_API_KEY`** — required for the comparison step. Without it,
  everything through content-fetching still works; the LLM step raises a
  clear `RuntimeError` rather than failing silently.

Everything else in `.env.example` has a working default — see the
[full reference table](#environment-variables-reference) below.

Confirm the install worked:

```bash
python -m pytest tests/ -q
```

You should see `252 passed`.

---

## Local setup — frontend

In a second terminal:

```bash
cd webapp/frontend
npm install
cp .env.example .env
npm run dev
```

Leave `webapp/frontend/.env` empty for local dev — Vite's dev server proxies
`/api/*` requests straight to `http://localhost:5000` (see
`webapp/frontend/vite.config.js`), so you don't need `VITE_API_URL` until
you deploy.

The dev server starts on `http://localhost:5173`.

---

## Smoke test: confirm everything works locally

With the backend running (`python webapp/backend/app.py`, or just running
the frontend which talks to it) in one terminal and the frontend (`npm run
dev`) in another:

1. Open `http://localhost:5173`.
2. Click **"Load sample"** (uses `data/sample_input.json`) or paste your own
   text with a `[1]`-style reference list.
3. Click **check citations**. You should see live progress events, then a
   report with a verdict per sentence.

Or skip the UI entirely and run the CLI directly:

```bash
python run.py --input data/sample_input.json --output output/report.json
```

This prints a human-readable summary to the console and writes the full
JSON report to `output/report.json`.

---

## Deploying — step by step

The backend makes several sequential external API calls per claim
(Crossref → NCBI → content fetch → OpenAI) with retries — a full
multi-citation document can take 1-2 minutes, which is too long for
serverless platforms with strict execution timeouts. Backend and frontend
are deployed to two different kinds of platform for this reason:
**Render** (a persistent server, handles the long-running work) for the
backend, and **Vercel** (static hosting) for the frontend.

### Step 1 — Push to GitHub

Push this repo to a GitHub repository. Both Render and Vercel deploy by
connecting to a GitHub repo.

### Step 2 — Deploy the backend to Render

1. In Render, click **New → Web Service** and connect your GitHub repo.
   Render should auto-detect `render.yaml` in the repo root and pre-fill the
   build/start commands.
2. Render will prompt for the environment variables marked `sync: false` in
   `render.yaml`. Set at minimum:
   - `CONTACT_EMAIL`
   - `OPENAI_API_KEY`
   - `NCBI_API_KEY` — optional, leave blank if you don't have one
   - `API_KEY` — optional, leave blank for now (see [Step 4](#step-4---optional-lock-down-the-api-with-an-api-key))
3. Leave `ALLOWED_ORIGIN` at its default (`*`) for now — you'll lock this
   down in Step 3 once you have your frontend's real URL.
4. Deploy. Note the live URL Render gives you, e.g.
   `https://citation-fidelity-checker-api.onrender.com`.
5. Confirm it's alive:
   ```bash
   curl https://YOUR-RENDER-URL.onrender.com/api/health
   # {"status": "ok"}
   ```

**Note on Render's free tier:** it sleeps after inactivity, so the first
request after idle time takes ~30-60s to cold-start. The frontend already
pings `/api/health` on page load to warm the instance early — fine for a
demo; upgrade to a paid instance before any real usage where that matters.

**Note on the gunicorn timeout:** `render.yaml`'s start command is
`gunicorn --timeout 600 --chdir webapp/backend app:app`. Gunicorn's own
default worker timeout is 30 seconds, which is far too short for a
multi-citation document. If you ever rebuild this service by hand instead
of letting Render read `render.yaml`, keep `--timeout 600` in the Start
Command field (Render → your service → Settings → Start Command), or long
documents will fail with a dropped connection that looks like a CORS bug
but isn't.

### Step 3 — Deploy the frontend to Vercel

1. In Vercel, click **New Project** → connect the same GitHub repo → set
   the **root directory** to `webapp/frontend`.
2. Add an environment variable: `VITE_API_URL` = your Render backend URL
   from Step 2 (no trailing slash), e.g.
   `https://citation-fidelity-checker-api.onrender.com`.
3. Deploy. Note the live Vercel URL, e.g. `https://your-app.vercel.app`.

### Step 4 — Close the CORS loop

Back in Render, set `ALLOWED_ORIGIN` to your live Vercel URL from Step 3,
then redeploy. The exact value matters:

- Full origin, **`https://` scheme, no trailing slash** — e.g.
  `https://your-app.vercel.app`, not `your-app.vercel.app` and not
  `https://your-app.vercel.app/`.
- A trailing slash is the classic gotcha: the browser's `Origin` header
  never has one, so a mismatched value silently drops the CORS header and
  the browser blocks every request with *"No 'Access-Control-Allow-Origin'
  header is present."* The backend tolerates trailing-slash/case drift, but
  the origin itself still has to be correct.

Confirm from the actual deployed frontend (not curl — CORS is a
browser-enforced check) that requests succeed.

### Step 5 (optional) — Lock down the API with an API key

By default, `/api/analyze`, `/api/analyze-text`, and `/api/recheck` (the
endpoints that trigger real, paid Crossref/NCBI/OpenAI calls) are open —
fine while you're the only user. Before sharing the URL more broadly:

1. **Render:** set `API_KEY` to a long random string (e.g.
   `openssl rand -hex 32`). Redeploy.
2. **Vercel:** set `VITE_API_KEY` to the exact same string. Redeploy.
3. Both sides must match, or every request 401s. Both sides unset (the
   default) means no auth at all — that's a valid, intentional state for
   local dev or an internal-only deployment, not a bug.

Per-IP rate limiting (`RATE_LIMIT_ANALYZE`, `RATE_LIMIT_RECHECK` — see the
[reference table](#environment-variables-reference)) is **always on**,
independent of whether `API_KEY` is set, since it requires no frontend
change.

---

## Environment variables reference

### Backend (`.env`)

| Variable | Required? | Default | What it does |
|---|---|---|---|
| `CONTACT_EMAIL` | **Yes** | — | Identifies you to Crossref/NCBI for better rate limits. App refuses to run without it. |
| `OPENAI_API_KEY` | **Yes** (for verdicts) | — | OpenAI key for the comparison call. |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Swap models without code changes. |
| `LLM_SEED` | No | `42` | Fixed seed for the primary temp-0 comparison call — best-effort determinism across repeated runs, not a hard guarantee. |
| `NCBI_API_KEY` | No | — | Raises NCBI E-utilities limits from 3 req/sec to 10 req/sec. |
| `API_KEY` | No | — (auth off) | Shared secret required as `X-API-Key` on the three cost-bearing endpoints, when set. |
| `RATE_LIMIT_ANALYZE` | No | `10 per hour` | Per-IP limit on `/api/analyze` and `/api/analyze-text`. |
| `RATE_LIMIT_RECHECK` | No | `30 per hour` | Per-IP limit on `/api/recheck`. |
| `JOB_TTL_SECONDS` | No | `3600` | How long a finished/running async job stays in memory before being purged. |
| `ALLOWED_ORIGIN` | No | `*` | CORS origin allowed to call the API. Set to your real frontend URL in production. |

### Frontend (`webapp/frontend/.env`)

| Variable | Required? | Default | What it does |
|---|---|---|---|
| `VITE_API_URL` | Only in production | — (relative `/api/...`, dev-proxied) | Your deployed backend's base URL. |
| `VITE_API_KEY` | Only if backend's `API_KEY` is set | — | Must match the backend's `API_KEY` exactly. |

---

## API reference

All routes are on the Flask backend (`webapp/backend/app.py`). Routes
marked 🔒 require `X-API-Key` when `API_KEY` is set on the backend, and
are rate-limited per IP.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness check, also used to warm a sleeping Render instance |
| GET | `/api/metrics` 🔒 | Summarizes the LLM call log: error rate, latency (avg/p50/p95/max) — see [Observability](#observability) |
| GET | `/api/download-log` 🔒 | Downloads `output/llm_call_log.jsonl` directly — see [Observability](#observability); exists for Render's free tier, which has no persistent disk or file browser |
| GET | `/api/sample` | Returns `data/sample_input.json`, for a "Load sample" button |
| POST | `/api/analyze` 🔒 | Synchronous: takes a pre-parsed `{reference_list, sentences}` body, returns the full report directly (content verdicts + metadata `bibliography_status`) |
| POST | `/api/parse-text` | Parses raw pasted text into `{reference_list, sentences}` without running the pipeline |
| POST | `/api/analyze-text` 🔒 | Async: takes raw pasted `{text}`, starts a background job, returns a `job_id` immediately; the job's final report includes both content verdicts and metadata `bibliography_status` |
| GET | `/api/progress/<job_id>` | Poll a job started via `/api/analyze-text` for live events + the final report |
| POST | `/api/recheck` 🔒 | Re-runs the pipeline for one sentence from an already-checked document |

---

## Testing

All 252 tests run against **mocked HTTP responses and a mocked OpenAI
client** — no live network calls, no API costs, no dependency on external
services being up.

```bash
python -m pytest tests/ -v
```

| Test file | What it specifically verifies |
|---|---|
| `test_crossref.py` | 404 is *not* retried; 5xx/429 *are*, with backoff; retry exhaustion and malformed JSON both surface as `status="error"` |
| `test_pmc_id_converter.py` | Empty-records and in-band record-level errors both map to `not_indexed`, distinct from `error` |
| `test_content_fetcher.py` | PMCID-present-but-no-retrievable-full-text falls back to abstract; no-IDs case makes zero network calls |
| `test_content_fetcher_guards.py` | Abstract-only usability checks (`is_usable_abstract`) — too-short or clearly non-substantive abstracts are rejected rather than sent to the LLM as if they were full text |
| `test_llm_wrapper.py` | Structured Outputs schema matches the prompt's declared fields exactly and is sent on every call; verbatim-quote guard; SDK's own internal retry is disabled (`max_retries=0`) to avoid double-backoff against our own retry wrapper |
| `test_pipeline.py` | Each stage's failure/edge case correctly short-circuits to the right final verdict; a DOI cited by multiple sentences is only fetched once per run |
| `test_aggregator.py` | Verdict grouping uses labels that don't appear anywhere in the module's own code — confirms no hardcoded taxonomy |
| `test_text_parser.py` / `test_citation_parser.py` | Raw-text → reference-list/sentence parsing, citation-marker extraction; reference-list entries carry the raw bibliography text for the metadata module |
| `test_api.py` | Webapp routes end-to-end, including API-key auth, rate limiting, and job-store TTL purging |
| `test_call_logger.py` / `test_metrics.py` | Every LLM call is logged with a latency; the log summarizer computes correct error rate and percentiles, and skips corrupted lines without going down |
| `test_run.py` | CLI input schema validation |
| `test_metadata_extraction.py` | Raw bibliography entry → structured record, against both the spec's own example and a real reference from this project's test manuscript; null vs. empty-string handling |
| `test_metadata_crossref.py` | DOI fetch and title+author search, against mocked Crossref responses; a weak title match returns `resolved` with a low score, not a silent `not_found` |
| `test_metadata_resolution.py` | Every branch of the spec's resolution flowchart — DOI match, no-DOI fallback, wrong-article rescue, technical failures — 12 tests, one per branch |
| `test_metadata_checks_phase_a.py` | Checks 1, 2, 3, 4, 5, 13, 14 — includes two regression tests for bugs caught during development (check 2 grabbing the wrong word, then grabbing an unrelated number like a year) |
| `test_metadata_checks_phase_b.py` | Checks 7, 9, 10, 11 — including check 11's per-ISSN sample caching across references sharing a journal |
| `test_metadata_checks_phase_c.py` | Check 8 (displaced authorship) and check 14's author-omission half |
| `test_metadata_report.py` | The spec's explicit acceptance criteria directly: a reference cited by 5 sentences produces one entry not five; `miscited` never blocks phase 2 |

For a live sanity check beyond mocked tests, `guard_check.py` re-verifies
quote-guard behavior against the output of a real run (`output/report.json`
+ `output/llm_call_log.jsonl`) rather than mocks — run it after a real CLI
or webapp run if you want to double-check the guard held up in practice.

---

## Observability

Every LLM call is logged to `output/llm_call_log.jsonl` (one JSON line per
call, regardless of outcome), including a `duration_seconds` field. Rather
than reading that file by hand, hit:

```bash
curl https://YOUR-BACKEND-URL/api/metrics
```

```json
{
  "total_calls": 42,
  "error_count": 1,
  "error_rate": 0.0238,
  "status_counts": {"verdict": 41, "system_error": 1},
  "latency_seconds": {"avg": 2.1, "p50": 1.9, "p95": 4.3, "max": 5.8},
  "calls_missing_duration": 0
}
```

Add `?limit=N` to summarize only the most recent `N` calls (default 500) —
useful for a "how's it doing right now" check versus the full history.

### Getting the raw log off a Render free-tier instance

Render's free tier has no persistent disk and no built-in file browser —
the only way to reach a file inside a running instance is the Shell tab's
`cat output/llm_call_log.jsonl`, which is painful for anything beyond a
trivially short log, and useless once you've scrolled past the terminal's
buffer. The **"Download LLM call log" button** on the report screen (next
to "Download results") solves this: it hits `/api/download-log` and saves
the file straight from the browser — reproduce the issue, click the
button, done.

Same ephemeral-filesystem caveat applies either way: the log only has
whatever's accumulated since the instance's last restart/redeploy/spin-down
— download it before that happens, not after.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "No 'Access-Control-Allow-Origin' header is present" in the browser console | `ALLOWED_ORIGIN` doesn't exactly match the frontend's origin | Check for a trailing slash or missing `https://`; see [Step 4](#step-4---close-the-cors-loop) |
| Long documents fail with "Could not reach the checker service," short ones work fine | Gunicorn's 30s default timeout killed the worker mid-request | Confirm `--timeout 600` is actually in the Render Start Command |
| First request after idle time is very slow | Render free tier cold start (~30-60s) | Expected; the frontend pre-warms `/api/health` on page load. Upgrade the Render plan if this matters for real usage |
| `/api/analyze` returns 401 | `API_KEY` is set on the backend but `VITE_API_KEY` on the frontend is missing or doesn't match | Set both to the exact same value and redeploy both |
| `/api/analyze-text` returns 429 | Per-IP rate limit hit | Wait, or raise `RATE_LIMIT_ANALYZE` / `RATE_LIMIT_RECHECK` |
| `RuntimeError: CONTACT_EMAIL is not set` | Missing from `.env` | Required, not optional — see [Local setup — backend](#local-setup--backend) |
| A sentence's verdict changes between otherwise-identical runs | Normal, rare, and expected | `LLM_SEED` gives best-effort determinism, not a hard guarantee — OpenAI documents this. If it's happening often on the *same* sentence, that's worth investigating as a genuinely borderline case, not just noise |

---

## Design decisions worth knowing about

- **Every "this is just information, not a failure" outcome gets its own
  status** — a DOI that doesn't exist, a DOI not indexed in PubMed, a PMCID
  with no retrievable full text. None of these are exceptions; they're all
  final, valid results that flow through to the report as their own verdict
  labels. Only genuinely transient failures (timeouts, 5xx, rate limits)
  trigger retries.
- **A DOI cited by more than one sentence is only resolved and fetched
  once per run.** `SourceCache` in `src/pipeline.py` caches the full
  Crossref → PMC ID → content-fetch chain per DOI, with per-DOI locking so
  different DOIs never block each other.
- **The LLM response shape is enforced by the API itself, not just parsed
  hopefully.** `response_format` in `src/llm_wrapper.py` passes a strict
  JSON schema mirroring the comparison prompt's declared output fields
  exactly, so a malformed or off-schema response is far less likely to slip
  through in the first place.
- **The SDK's own internal retry is deliberately disabled** (`max_retries=0`
  on the OpenAI client) because our own `tenacity`-based retry around the
  comparison call already handles the same error types with its own
  backoff — leaving both on would stack two independent retry/backoff
  layers on a single failure and make `LLM_MAX_RETRIES` not mean what it
  says.
- **Malformed LLM JSON is retried like a network error**, not just logged —
  a garbled response is often as recoverable as a dropped connection.
- **The verdict taxonomy is not hardcoded anywhere below `pipeline.py`.**
  `aggregator.py` groups by whatever string it's given; if the final
  verdict categories change, `pipeline.py` is the only file that needs
  edits.
- **The in-memory async job store (`_JOBS` in `webapp/backend/app.py`) is
  safe as-is for a single gunicorn worker** (the default, and what
  `render.yaml` deploys) and purges entries older than `JOB_TTL_SECONDS`
  opportunistically when a new job is created. It is explicitly not
  designed to survive multiple workers or a restart — see
  [Known limitations](#known-limitations--open-items).

---

## Known limitations / open items

- **No caching across separate runs.** `SourceCache` avoids redundant
  fetches *within* one document's run, but a fresh run of a different
  document that happens to cite an already-checked DOI will fetch it again
  from scratch. Worth a persistent cache (e.g. Redis or a small DB) if this
  becomes a cost concern.
- **The async job store doesn't survive a restart or scale past one
  worker.** Fine for the current single-worker Render deployment; would
  need to move to something like Redis before horizontally scaling.
- **Not every PMCID has retrievable full-text XML via EFetch**
  (open-access status dependent). The fallback to abstract is built and
  tested, but how often this happens depends on the mix of sources a real
  document cites.
- **The comparison prompt has one diagnosed, unfixed gap:** a claim that
  bundles a correct figure from its cited source with a different,
  uncited source's numbers can occasionally still verdict "supported." A
  fix is written (`comparison_system_prompt.txt`, not yet applied) but is
  pending review before going live.
- **The metadata module (`src/metadata/`) is fully wired into both entry
  points** (CLI and webapp), running concurrently with the content
  pipeline via `src/analysis_runner.py`. See `MVP1_METADATA_MODULE_NOTES.md`
  for two build-time decisions and a real precision issue found in check 8
  (displaced authorship) worth validating against the 10 hand-verified
  documents before trusting its output on a bibliography that heavily
  cites one overlapping research group.
- **Reference-string parsing in `src/metadata/extraction.py` targets APA
  style specifically** (matching this project's own sample/test documents)
  — Vancouver, MLA, and Chicago styles will parse partially at best.
