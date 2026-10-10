from src.stage03_d01 import validate_stage03_pp01_fixture


def test_stage03_pp01_fixture_passes_all_rules():
    result = validate_stage03_pp01_fixture()

    assert result["status"] == "pass"
    assert result["passed_rules"] == 10
    assert result["failed_rules"] == []
    assert result["journal_entries"] >= 2
    assert set(result["rules"]).issuperset({
        "R-01", "R-02", "R-03", "R-04", "R-05",
        "R-06", "R-07", "R-08", "R-09", "R-10"
    })


def test_stage03_validator_rejects_duplicate_paid_call():
    journal = [
        {"case_id": "PP-01", "stage": "fetch", "paid_call": True, "call_id": "c1"},
        {"case_id": "PP-01", "stage": "fetch", "paid_call": True, "call_id": "c1"},
    ]
    result = validate_stage03_pp01_fixture(journal=journal)

    assert result["status"] == "fail"
    assert "R-07" in result["failed_rules"]
