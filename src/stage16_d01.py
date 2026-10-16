"""Stage 16 / D-01 v3.6 adapter validation for the backlog T-16 contract."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.claims_v36 import claims_v36
from src.derive_pair_verdict_v36 import derive_pair_verdict_v36
from src.judge_v30 import judge_v30

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = ROOT / "validation" / "pp01_stage16_fixture.json"

STAGE16_RULES = [
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
            "id": "PP01-T16",
            "claim_text": "Exercise reduced CRP and IL-6 after 12 weeks.",
            "evidence_text": "After 12 weeks, the exercise programme reduced plasma C-reactive protein and interleukin-6.",
            "decomposition": "01b",
        }
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_stage16_pp01_fixture(
    claim_text: str | None = None,
    evidence_text: str | None = None,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    resolved_claim = claim_text if claim_text is not None else fixture.get("claim_text", "")
    resolved_evidence = evidence_text if evidence_text is not None else fixture.get("evidence_text", "")

    atoms = claims_v36(resolved_claim, decomposition=fixture.get("decomposition", "01b"))
    pair_verdict = derive_pair_verdict_v36(
        resolved_claim,
        resolved_evidence,
        source_type=fixture.get("source_type", "abstract"),
        citation_context=fixture.get("citation_context", "sole"),
    )
    judge = judge_v30(
        resolved_claim,
        [{"location": "abstract", "text": resolved_evidence}],
        source_type=fixture.get("source_type", "abstract"),
        citation_context=fixture.get("citation_context", "sole"),
        case_id=fixture.get("case_id", "PP01-T16"),
    )

    rules: dict[str, bool] = {}
    rules["R-01"] = isinstance(atoms, list) and len(atoms) >= 1
    rules["R-02"] = all("id" in item and "text" in item for item in atoms)
    rules["R-03"] = all(item.get("coverage") == "v3.6" for item in atoms)
    rules["R-04"] = pair_verdict["offline_mock"] is True
    rules["R-05"] = pair_verdict["coverage"] == "v3.6"
    rules["R-06"] = pair_verdict["verdict"] in {"supported", "unsupported", "uncertain"}
    rules["R-07"] = judge["offline_mock"] is True
    rules["R-08"] = judge["prompt_version"] in {"judge_v2.1", "judge_v2.2.2", "judge_v3.7"}
    rules["R-09"] = judge["final_verdict"] in {"supported", "unsupported", "uncertain"}
    rules["R-10"] = bool(judge.get("call_log"))

    failed_rules = [rule_id for rule_id, passed in rules.items() if not passed]
    passed_rules = [rule_id for rule_id, passed in rules.items() if passed]

    return {
        "status": "pass" if not failed_rules else "fail",
        "rules": rules,
        "passed_rules": len(passed_rules),
        "failed_rules": failed_rules,
        "fixture_id": fixture.get("id", "PP01-T16"),
        "claim_atoms": atoms,
        "pair_verdict": pair_verdict,
        "judge": judge,
    }


__all__ = ["STAGE16_RULES", "validate_stage16_pp01_fixture"]
