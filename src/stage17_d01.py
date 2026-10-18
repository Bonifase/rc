"""Stage 17 / D-01 definitive swap-in validation for the backlog T-17 contract."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.adapter_registry import apply_prompt_swap, get_adapter_config

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage17_fixture.json"

STAGE17_RULES = [
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
        return {
            "id": "PP01-T17",
            "selected_version": "judge_v2.2.2",
            "mode": "definitive",
        }
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_stage17_pp01_fixture(
    version: str | None = None,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    resolved_version = version if version is not None else fixture.get("selected_version", "judge_v2.2.2")
    config = get_adapter_config()
    swap = apply_prompt_swap("judge", resolved_version)
    definitive = config.get("definitive_components", {})

    rules: dict[str, bool] = {}
    rules["R-01"] = bool(resolved_version)
    rules["R-02"] = swap["selected_version"] in config["supported_versions"]
    rules["R-03"] = swap["mode"] == "offline"
    rules["R-04"] = bool(swap["prompt_hash"])
    rules["R-05"] = "judge_v2.2.2" in config["supported_versions"]
    rules["R-06"] = config["swap_policy"].get("definitive") == "judge_v2.2.2"
    rules["R-07"] = definitive.get("judge_adapter") == "judge_hybrid_v2_2_2"
    rules["R-08"] = bool(definitive.get("winner_prompt_hash"))
    rules["R-09"] = config.get("extended_retrieval_enabled") is False
    rules["R-10"] = swap["selected_version"] in {"judge_v2.1", "judge_v2.2.2"}

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id", "PP01-T17"),
        "selected_version": swap["selected_version"],
        "prompt_hash": swap["prompt_hash"],
        "config": config,
    }


__all__ = ["STAGE17_RULES", "validate_stage17_pp01_fixture"]
