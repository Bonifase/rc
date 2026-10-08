from src.stage07_d01 import validate_stage07_pp01_fixture


def test_stage07_pp01_fixture_passes_all_rules():
    result = validate_stage07_pp01_fixture()

    assert result["status"] == "pass"
    assert result["passed_rules"] == 10
    assert result["failed_rules"] == []
    assert result["checked_reference_count"] == 2
    assert result["resolved_reference_count"] == 2
    assert result["no_source_count"] == 0
    assert result["reference_records"][0]["journal"] == "Journal of Comparative Physiology"
    assert result["reference_records"][0]["retraction_status"] == "not_retracted"
    assert result["reference_records"][0]["source"] == "PMC"
    assert result["reference_records"][0]["extended"] is True
    assert set(result["rules"]).issuperset({
        "R-01", "R-02", "R-03", "R-04", "R-05", "R-06", "R-07", "R-08", "R-09", "R-10"
    })


def test_stage07_validator_rejects_missing_reference_resolution():
    body = "Cortisol drives the stress response through the glucocorticoid receptor [1].\n\nReferences\n[1] Smith, A. L., & Jones, B. (2020). Stress signaling in teleost fish. Journal of Comparative Physiology, 210(4), 345-360. https://doi.org/10.1000/one"
    result = validate_stage07_pp01_fixture(text=body)

    assert result["status"] == "fail"
    assert "R-09" in result["failed_rules"]
