from src.stage09_d01 import validate_stage09_pp01_fixture


def test_stage09_pp01_fixture_passes_all_rules():
    result = validate_stage09_pp01_fixture()

    assert result["status"] == "pass"
    assert result["passed_rules"] == 10
    assert result["failed_rules"] == []
    assert result["verdict"] == "supported"
    assert result["source_type"] == "abstract"
    assert result["citation_context"] == "sole"
    assert set(result["rules"]).issuperset({
        "R-01", "R-02", "R-03", "R-04", "R-05", "R-06", "R-07", "R-08", "R-09", "R-10"
    })


def test_stage09_marks_mismatch_as_unsupported():
    result = validate_stage09_pp01_fixture(
        claim_text="Cortisol stress signaling is conserved in teleosts.",
        evidence_text="This study focuses on mammalian metabolic pathways and not on teleost stress signaling.",
        source_type="abstract",
        citation_context="sole",
    )

    assert result["status"] == "pass"
    assert result["verdict"] == "unsupported"
    assert result["primary_family"] == "off_target_relevance"
