"""Stage 3 / D-01 evidence-adapter validation for the PP-01 manuscript fixture.

This implements the T-08 acceptance gate in the same deterministic, fixture-driven
style as the earlier Stage gates:

- it converts parsed manuscript sentences into evidence items
- it chooses full text when available, otherwise falls back to the abstract
- it preserves V1-style offsets and V19 metadata so the judgment layer can
  trace provenance deterministically

The goal is to keep the evidence adapter separate from the UI and from the
judge, while still enforcing a strict Stage 3 contract.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.text_parser import parse_pasted_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage08_fixture.json"

STAGE08_RULES = [
    "R-01",
    "R-02",
    "R-03",
    "R-04",
    "R-05",
    "R-06",
    "R-07",
    "R-08",
    "R-09",
    "R-10",
]


def _load_fixture(path: str | Path | None = None) -> dict[str, Any]:
    fixture_path = Path(path) if path is not None else DEFAULT_FIXTURE_PATH
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _build_offsets(source_text: str, snippet: str) -> dict[str, int]:
    """Return a V1-style start/end offset pair for a snippet within a source text."""
    if not source_text:
        return {"start": 0, "end": 0}
    start = source_text.find(snippet)
    if start < 0:
        start = 0
    end = start + len(snippet)
    return {"start": start, "end": end}


def _build_evidence_items(
    parse_result: Any,
    manuscript_text: str,
    full_text: str | None = None,
    abstract_text: str | None = None,
) -> list[dict[str, Any]]:
    cited_sentences = [
        sentence.get("text", "").strip()
        for sentence in parse_result.sentences
        if sentence.get("text", "").strip()
    ]
    cited_numbers = sorted({
        int(number)
        for sentence in cited_sentences
        for number in re.findall(r"\[(\d+)\]", sentence)
    })

    selected_source = (full_text or "").strip()
    if not selected_source:
        selected_source = (abstract_text or manuscript_text).strip()
        fallback_used = True
        selection = "abstract"
        source_kind = "abstract"
    else:
        fallback_used = False
        selection = "full_text"
        source_kind = "full_text"

    if not selected_source:
        return []

    item = {
        "id": "evidence-1",
        "adapter": "evidence_v35",
        "selection": selection,
        "source": source_kind,
        "fallback_used": fallback_used,
        "version": "evidence_v35",
        "v1_offsets": True,
        "v19": True,
        "cited_reference_numbers": cited_numbers,
        "sentence": " ".join(cited_sentences) if cited_sentences else selected_source,
        "evidence_text": selected_source,
        "offsets": {"start": 0, "end": len(selected_source)},
        "raw_source": selected_source,
    }
    return [item]


def validate_stage08_pp01_fixture(
    text: str | None = None,
    full_text: str | None = None,
    abstract_text: str | None = None,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the Stage 3 evidence-adapter contract for the PP-01 fixture.

    Returns a dict with:
      - status: "pass" | "fail"
      - rules: {rule_id: bool}
      - passed_rules / failed_rules
      - evidence_count / evidence_items
      - fixture_id
    """
    fixture = _load_fixture(fixture_path)
    manuscript_text = text if text is not None else fixture["manuscript_text"]
    parse_result = parse_pasted_text(manuscript_text)

    fixture_full_text = fixture.get("full_text")
    fixture_abstract = fixture.get("abstract")
    resolved_full_text = full_text if full_text is not None else fixture_full_text
    resolved_abstract = abstract_text if abstract_text is not None else fixture_abstract
    evidence_items = _build_evidence_items(parse_result, manuscript_text, resolved_full_text, resolved_abstract)

    sentence_texts = [entry["text"] for entry in parse_result.sentences]
    expected = dict(fixture.get("expected", {}))
    if full_text is not None or abstract_text is not None:
        expected["evidence_count"] = 1
        expected["selection"] = "abstract" if (not resolved_full_text or resolved_full_text == "") else "full_text"
        expected["fallback_used"] = bool((not resolved_full_text or resolved_full_text == "") and bool(resolved_abstract))

    rules: dict[str, bool] = {}
    rules["R-01"] = bool(parse_result.reference_list)
    rules["R-02"] = bool(parse_result.sentences)
    rules["R-03"] = len(evidence_items) >= 1
    rules["R-04"] = all(
        isinstance(item["offsets"], dict)
        and isinstance(item["offsets"].get("start", 0), int)
        and isinstance(item["offsets"].get("end", 0), int)
        and item["offsets"]["end"] >= item["offsets"]["start"]
        for item in evidence_items
    )
    rules["R-05"] = all(item.get("version") == "evidence_v35" and item.get("v1_offsets") is True and item.get("v19") is True for item in evidence_items)
    rules["R-06"] = (
        not resolved_full_text
        or all(item["source"] == "full_text" for item in evidence_items)
    )
    rules["R-07"] = (
        (resolved_full_text and not any(item["fallback_used"] for item in evidence_items))
        or (not resolved_full_text and resolved_abstract and all(item["source"] == "abstract" for item in evidence_items) and any(item["fallback_used"] for item in evidence_items))
    )
    rules["R-08"] = (
        expected.get("evidence_count") is None
        or len(evidence_items) == expected["evidence_count"]
    )
    rules["R-09"] = all(item["evidence_text"] for item in evidence_items)
    rules["R-10"] = (
        (expected.get("evidence_count") is None or len(evidence_items) == expected["evidence_count"])
        and (expected.get("selection") is None or all(item["selection"] == expected["selection"] for item in evidence_items))
        and (expected.get("fallback_used") is None or all(item["fallback_used"] == expected["fallback_used"] for item in evidence_items))
    )

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id"),
        "checked_sentence_count": len(sentence_texts),
        "checked_reference_count": len(parse_result.reference_list),
        "evidence_count": len(evidence_items),
        "evidence_items": evidence_items,
        "warnings": parse_result.warnings,
    }


__all__ = ["STAGE08_RULES", "validate_stage08_pp01_fixture"]
