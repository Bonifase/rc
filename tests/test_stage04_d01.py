from src.stage04_d01 import validate_stage04_pp01_fixture


def test_stage04_pp01_fixture_passes_all_rules():
    result = validate_stage04_pp01_fixture()

    assert result["status"] == "pass"
    assert result["passed_rules"] == 10
    assert result["failed_rules"] == []
    assert result["candidate_count"] == 194
    assert set(result["rules"]).issuperset({
        "R-01", "R-02", "R-03", "R-04", "R-05",
        "R-06", "R-07", "R-08", "R-09", "R-10"
    })


def test_stage04_validator_rejects_missing_author_year_candidates():
    text = "The pathway is important [1].\n\nReferences\n[1] Smith, A. (2020). Example. Journal. 10.1000/one"
    result = validate_stage04_pp01_fixture(text=text)

    assert result["status"] == "fail"
    assert "R-08" in result["failed_rules"]
