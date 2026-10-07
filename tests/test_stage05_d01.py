from src.stage05_d01 import validate_stage05_pp01_fixture


def test_stage05_pp01_fixture_passes_all_rules():
    result = validate_stage05_pp01_fixture()

    assert result["status"] == "pass"
    assert result["passed_rules"] == 10
    assert result["failed_rules"] == []
    assert result["checked_reference_count"] == 2
    assert result["checked_sentence_count"] == 2
    assert set(result["rules"]).issuperset({"R-01", "R-02", "R-03", "R-04", "R-05", "R-06", "R-07", "R-08", "R-09", "R-10"})


def test_stage05_validator_rejects_missing_reference_block():
    body = "The transcriptome was altered in trout [1]."
    result = validate_stage05_pp01_fixture(text=body)

    assert result["status"] == "fail"
    assert "R-03" in result["failed_rules"]
