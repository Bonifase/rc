"""
Runs the content-comparison pipeline (src/pipeline.py) and the metadata
module (src/metadata/) concurrently. They're fully independent -- the
metadata module's own design intent is "no dependency on v16 or the
content pipeline" -- and both spend most of their wall-clock time waiting
on external HTTP calls, so two threads instead of sequential calls
actually cuts real latency, not just looks tidier.

Shared by run.py (CLI) and webapp/backend/app.py so this isn't duplicated
in two places with a risk of drifting apart.
"""
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from src.pipeline import process_sentences, PairResult
from src.progress import ProgressTracker
from src.metadata.extraction import build_declared_references
from src.metadata.document import MetadataDoc
from src.metadata.report import build_report as build_metadata_report

EMPTY_METADATA_REPORT = {
    "bibliography_status": [],
    "document_level_findings": [],
    "not_evaluated_checks": [],
}


def run_content_and_metadata(
    sentences: list[dict],
    reference_list: dict[int, dict | str],
    reference_entries: list[dict],
    body_text: str,
    contact_email: str,
    progress: Optional[ProgressTracker] = None,
) -> tuple[list[PairResult], dict]:
    """
    Returns (pair_results, metadata_report). A content-pipeline failure
    raises, as before this existed. A metadata-module failure does NOT --
    it's a secondary, additive axis, so its result comes back as
    EMPTY_METADATA_REPORT plus an "error" key, and the primary content
    verdicts still reach the caller.

    reference_entries is the richer list-of-dicts shape (each optionally
    carrying "raw" bibliography text); reference_list is the {number: entry}
    dict shape process_sentences expects. An entry may be a plain DOI string
    or a dict with doi / title / first_author / publication_year / raw --
    identifier-free citations (no DOI) reach the pipeline this way. Callers
    already have both from parsing, so this just takes what each half needs
    rather than re-deriving one from the other.
    """
    declared_references = build_declared_references(reference_entries)
    doc = MetadataDoc(sentences=sentences, body_text=body_text)

    with ThreadPoolExecutor(max_workers=2) as pool:
        content_future = pool.submit(process_sentences, sentences, reference_list, contact_email, progress=progress)
        metadata_future = pool.submit(build_metadata_report, doc, declared_references, contact_email=contact_email)

        pair_results = content_future.result()

        try:
            metadata_report = metadata_future.result()
        except Exception as exc:  # noqa: BLE001 -- metadata is additive; don't take down content results
            metadata_report = {**EMPTY_METADATA_REPORT, "error": f"Metadata module failed: {exc}"}

    return pair_results, metadata_report
