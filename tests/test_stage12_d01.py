from src.stage12_d01 import validate_stage12_pp01_fixture


def test_stage12_pp01_fixture_passes_all_rules():
    result = validate_stage12_pp01_fixture()

    assert result["status"] == "pass"
    assert result["passed_rules"] == 10
    assert result["failed_rules"] == []
    assert result["decision"]["final_verdict"] == "unsupported"
