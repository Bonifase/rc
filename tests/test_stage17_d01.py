from src.stage17_d01 import validate_stage17_pp01_fixture


def test_validate_stage17_pp01_fixture_passes_for_definitive_swap_contract():
    result = validate_stage17_pp01_fixture()
    assert result["status"] == "pass"
    assert result["selected_version"] == "judge_v3.7"
    assert result["config"]["swap_policy"]["definitive"] == "judge_v3.7"
