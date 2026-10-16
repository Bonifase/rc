from src.stage16_d01 import validate_stage16_pp01_fixture


def test_validate_stage16_pp01_fixture_passes_for_v36_adapter_contract():
    result = validate_stage16_pp01_fixture()
    assert result["status"] == "pass"
    assert result["judge"]["final_verdict"] in {"supported", "unsupported", "uncertain"}
    assert result["pair_verdict"]["coverage"] == "v3.6"
