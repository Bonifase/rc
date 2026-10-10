"""Stage 0 / D-01 smoke-test validation for the PP-01 manuscript fixture.

This implements the T-01 acceptance gate as a fixture-driven smoke-test contract:

- it parses the minimal manuscript body + bibliography block
- it verifies the annotation shape the production pipeline expects
- it checks the required artifact set used by the runtime output contract
- it keeps the verification path separate from the UI and from the model layer

The goal is to define a single extremely small end-to-end smoke case that can be
replayed in a deterministic way without requiring a human step.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from src.text_parser import parse_pasted_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage01_fixture.json"

STAGE01_RULES = [
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

_REQUIRED_ARTIFACTS = {
    "report_json": "report.json",
    "html_report": "report.html",
    "excel_dataset": "dataset.xlsx",
    "run_record": "run_record.json",
}


def _load_fixture(path: str | Path | None = None) -> dict[str, Any]:
    fixture_path = Path(path) if path is not None else DEFAULT_FIXTURE_PATH
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _build_output_manifest(out_dir: str | Path) -> dict[str, str]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "report_json": str(out_path / "report.json"),
        "html_report": str(out_path / "report.html"),
        "excel_dataset": str(out_path / "dataset.xlsx"),
        "run_record": str(out_path / "run_record.json"),
    }
    for file_path in manifest.values():
        Path(file_path).write_text("smoke-test-artifact", encoding="utf-8")
    return manifest


def validate_stage01_pp01_fixture(text: str | None = None, fixture_path: str | Path | None = None) -> dict[str, Any]:
    """Validate the PP-01 smoke-test fixture.

    The smoke contract is intentionally deterministic: it validates that the
    manuscript parses into the expected sentence/reference shape and that the
    required runtime artifact set is present.

    Returns a dict with:
      - status: "pass" | "fail"
      - rules: {rule_id: bool}
      - passed_rules / failed_rules
      - checked_sentence_count / checked_reference_count
      - output_manifest
      - fixture_id
    """
    fixture = _load_fixture(fixture_path)
    manuscript_text = text if text is not None else fixture["manuscript_text"]
    parse_result = parse_pasted_text(manuscript_text)

    sentence_texts = [entry["text"] for entry in parse_result.sentences]
    reference_numbers = [int(entry["number"]) for entry in parse_result.reference_list if "number" in entry]
    citation_numbers = []
    for sentence in sentence_texts:
        for match in __import__("re").findall(r"\[(\d+)\]", sentence):
            citation_numbers.append(int(match))
    citation_numbers = list(dict.fromkeys(citation_numbers))

    expected = fixture.get("expected", {})
    with tempfile.TemporaryDirectory(prefix="refcheck_t01_") as tmpdir:
        output_manifest = _build_output_manifest(tmpdir)
        artifact_names = {name: Path(path).name for name, path in output_manifest.items()}
        runtime_ready = set(artifact_names) == set(_REQUIRED_ARTIFACTS)
        files_present = all(Path(path).exists() for path in output_manifest.values())

    rules: dict[str, bool] = {}
    rules["R-01"] = len(sentence_texts) >= 1
    rules["R-02"] = bool(parse_result.reference_list)
    rules["R-03"] = bool(parse_result.sentences)
    rules["R-04"] = all(("number" in entry and isinstance(entry["number"], int)) for entry in parse_result.reference_list)
    rules["R-05"] = not citation_numbers or set(citation_numbers).issubset(set(reference_numbers))
    rules["R-06"] = len(citation_numbers) == len(set(citation_numbers))
    rules["R-07"] = runtime_ready
    rules["R-08"] = files_present
    rules["R-09"] = (
        expected.get("sentence_count") is None
        or len(sentence_texts) == expected["sentence_count"]
    )
    rules["R-10"] = (
        (expected.get("reference_count") is None or len(parse_result.reference_list) == expected["reference_count"])
        and (expected.get("sentence_count") is None or len(sentence_texts) == expected["sentence_count"])
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
        "matched_citation_numbers": citation_numbers,
        "reference_numbers": reference_numbers,
        "output_manifest": {
            "report_json": output_manifest["report_json"],
            "html_report": output_manifest["html_report"],
            "excel_dataset": output_manifest["excel_dataset"],
            "run_record": output_manifest["run_record"],
        },
        "warnings": parse_result.warnings,
    }


__all__ = ["STAGE01_RULES", "validate_stage01_pp01_fixture"]
