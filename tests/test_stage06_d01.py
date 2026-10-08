from src.stage06_d01 import validate_stage06_pp01_fixture


def test_stage06_pp01_fixture_passes_all_rules():
    result = validate_stage06_pp01_fixture()

    assert result["status"] == "pass"
    assert result["passed_rules"] == 10
    assert result["failed_rules"] == []
    assert result["checked_reference_count"] == 2
    assert result["checked_sentence_count"] == 2
    assert len(result["reconciled_claims"]) == 2
    assert set(result["rules"]).issuperset({
        "R-01", "R-02", "R-03", "R-04", "R-05", "R-06", "R-07", "R-08", "R-09", "R-10"
    })


def test_stage06_validator_rejects_unreconciled_claims():
    body = "The cytokine signal rises during stress [1].\n\nReferences\n[1] Smith, A. L. (2023). Stress signaling in tissue repair. Journal of Biology, 12(3), 201-220. https://doi.org/10.1000/alpha"
    result = validate_stage06_pp01_fixture(text=body)

    assert result["status"] == "fail"
    assert "R-06" in result["failed_rules"]
