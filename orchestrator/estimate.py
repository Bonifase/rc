"""Pre-flight for a manuscript: parse it and count the model calls a live run could make. No network, no spend."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.model_call_store import store_dir
from src.text_parser import parse_pasted_text

from .runner import _build_intake_manifest, _extract_manuscript_text


def estimate_run(source: str | Path) -> dict[str, Any]:
    text = _extract_manuscript_text(Path(source))
    parsed = parse_pasted_text(text)
    sentences = [{"id": item["id"], "text": item["text"]} for item in parsed.sentences]
    manifest = _build_intake_manifest(sentences, parsed.reference_list)

    pairs = len(manifest["citations"])
    return {
        "references_parsed": len(manifest["bibliography"]),
        "cited_sentences": len(sentences),
        "cited_pairs": pairs,
        "orphan_markers": sorted({item["marker"] for item in manifest["orphan_citations"]}),
        # Pairs whose source cannot be retrieved never reach the judge, so these are upper bounds.
        "judge_calls_minimum": 6 * pairs,
        "judge_calls_maximum": 12 * pairs,
        "report_text_calls_maximum": 2 * pairs,
        "model_calls_maximum": 14 * pairs,
        "call_budget": int(os.getenv("REFCHECK_MAX_MODEL_CALLS", "0") or 0) or None,
        "call_store": str(store_dir()),
        "live_judge": os.getenv("REFCHECK_LIVE_JUDGE", "").strip().lower() in {"1", "true", "yes", "on"},
        "warnings": [
            warning for warning in (
                "No references were parsed." if not manifest["bibliography"] else None,
                "No cited sentences were found." if not pairs else None,
            ) if warning
        ],
    }
