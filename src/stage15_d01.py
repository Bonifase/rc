"""Stage 15 / D-01 acceptance harness validation for the backlog T-15 contract."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.acceptance_harness import orchestrator_accept, run_verify_bundled

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage15_fixture.json"

STAGE15_RULES = [
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
            "id": "PP01-T15",
            "target_modes": ["replay", "live"],
            "manuscript_path": None,
        }
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_stage15_pp01_fixture(
    manuscript_path: str | None = None,
    fixture_path: str | Path | None = None,
    *,
    mode: str | None = None,
) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    resolved_path = manuscript_path or fixture.get("manuscript_path")
    replay = orchestrator_accept(mode="replay", manuscript_path=resolved_path)
    live = orchestrator_accept(mode="live", manuscript_path=resolved_path)
    bundled = run_verify_bundled()

    modes = [mode] if mode is not None else ["replay", "live"]
    replay_ok = replay["status"] == "accepted"
    live_ok = live["status"] == "accepted"
    bundled_ok = bundled["status"] == "passed"

    rules: dict[str, bool] = {}
    rules["R-01"] = isinstance(modes, list) and len(modes) >= 1
    rules["R-02"] = replay_ok
    rules["R-03"] = live_ok
    rules["R-04"] = replay["mode"] == "replay"
    rules["R-05"] = live["mode"] == "live"
    rules["R-06"] = bool(replay["prompt_versions_used"])
    rules["R-07"] = bool(live["prompt_versions_used"])
    rules["R-08"] = replay["offline_only"] is True
    rules["R-09"] = live["offline_only"] is True
    rules["R-10"] = bundled_ok

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id", "PP01-T15"),
        "replay": replay,
        "live": live,
        "bundled": bundled,
    }


__all__ = ["STAGE15_RULES", "validate_stage15_pp01_fixture"]
