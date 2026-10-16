from src.stage00_d01 import validate_stage00_pp01_fixture


def test_validate_stage00_pp01_fixture_passes_for_pinned_manifest():
    result = validate_stage00_pp01_fixture()
    assert result["status"] == "pass"
    assert result["fixture_id"] == "PP01-T00"
    assert "prompt_01" in result["artifact_hashes"]
    assert "pp01_master" in result["artifact_hashes"]
