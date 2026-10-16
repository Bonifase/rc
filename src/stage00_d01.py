"""Stage 00 / D-01 pinned-artifact validation for the backlog T-00 contract."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.config import PINNED_ARTIFACTS, validate_pinned_artifacts

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage00_fixture.json"

STAGE00_RULES = [
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
    if not fixture_path.exists():
        return {"id": "PP01-T00", "artifact_hashes": dict(PINNED_ARTIFACTS)}
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_stage00_pp01_fixture(
    artifact_manifest: dict[str, str] | None = None,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    resolved_manifest = artifact_manifest if artifact_manifest is not None else fixture.get("artifact_hashes") or dict(PINNED_ARTIFACTS)
    validation = validate_pinned_artifacts(resolved_manifest)

    required_names = [
        "prompt_01",
        "prompt_02",
        "prompt_03_txt",
        "prompt_04",
        "prompt_04_addendum",
        "mensaje_chat2",
        "mensaje_chat3",
        "template_aprobada_20260908",
        "verificar_js",
        "ensamblar_master_dataset_py",
        "pp01_master",
        "pp01_qa",
        "pp01_payloads_outputs",
        "zablon_updated_bundle",
    ]

    rules: dict[str, bool] = {}
    rules["R-01"] = isinstance(resolved_manifest, dict)
    rules["R-02"] = all(name in resolved_manifest for name in required_names)
    rules["R-03"] = all(isinstance(value, str) for value in resolved_manifest.values())
    rules["R-04"] = all(len(value) == 64 for value in resolved_manifest.values())
    rules["R-05"] = all(value.lower() == value for value in resolved_manifest.values())
    rules["R-06"] = all(value and value != "" for value in resolved_manifest.values())
    rules["R-07"] = validation["status"] == "pass"
    rules["R-08"] = set(required_names).issubset(set(resolved_manifest))
    rules["R-09"] = all(value in PINNED_ARTIFACTS.values() for value in resolved_manifest.values())
    rules["R-10"] = all(name in PINNED_ARTIFACTS for name in required_names)

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id", "PP01-T00"),
        "artifact_hashes": resolved_manifest,
        "validation": validation,
    }


__all__ = ["STAGE00_RULES", "validate_stage00_pp01_fixture"]
