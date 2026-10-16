from src.stage15_d01 import validate_stage15_pp01_fixture


def test_validate_stage15_pp01_fixture_passes_for_offline_acceptance_harness():
    result = validate_stage15_pp01_fixture()
    assert result["status"] == "pass"
    assert result["replay"]["status"] == "accepted"
    assert result["live"]["status"] == "accepted"
    assert result["bundled"]["status"] == "passed"
