import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "validation" / "fixtures" / "offline_simulated_sample"


def test_canonical_offline_fixture_is_committed_and_valid():
    report_path = FIXTURE_DIR / "report.json"
    run_record_path = FIXTURE_DIR / "run_record.json"

    assert report_path.exists(), "Canonical offline report fixture is missing"
    assert run_record_path.exists(), "Canonical offline run_record fixture is missing"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    run_record = json.loads(run_record_path.read_text(encoding="utf-8"))

    assert report["simulation"] is True
    assert report["offline"] is True
    assert report["judge_executed"] is True
    assert report["final_verdict"] in {"supported", "unsupported", "uncertain"}

    assert run_record["judge_executed"] is True
    assert run_record["offline_mock"] is True
    assert run_record["simulation"] is True
    assert run_record["final_verdict"] == report["final_verdict"]
    assert run_record["provider_calls"] == report["provider_calls"]
