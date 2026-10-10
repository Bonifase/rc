from src.stage14_d01 import validate_stage14_pp01_fixture


def test_stage14_pp01_fixture_passes_all_rules():
    result = validate_stage14_pp01_fixture()

    assert result["status"] == "pass"
    assert result["passed_rules"] == 10
    assert result["failed_rules"] == []
    assert result["paid_calls"] == 0
