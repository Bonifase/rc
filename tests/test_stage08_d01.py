from src.stage08_d01 import validate_stage08_pp01_fixture


def test_stage08_pp01_fixture_passes_all_rules():
    result = validate_stage08_pp01_fixture()

    assert result["status"] == "pass"
    assert result["passed_rules"] == 10
    assert result["failed_rules"] == []
    assert result["evidence_count"] == 1
    assert result["evidence_items"][0]["selection"] == "full_text"
    assert result["evidence_items"][0]["source"] == "full_text"
    assert result["evidence_items"][0]["offsets"]["start"] >= 0
    assert result["evidence_items"][0]["offsets"]["end"] > result["evidence_items"][0]["offsets"]["start"]
    assert result["evidence_items"][0]["fallback_used"] is False
    assert set(result["rules"]).issuperset({
        "R-01", "R-02", "R-03", "R-04", "R-05", "R-06", "R-07", "R-08", "R-09", "R-10"
    })


def test_stage08_falls_back_to_abstract_when_full_text_is_empty():
    body = "Cortisol stress signaling is conserved in teleosts [1].\n\nReferences\n[1] Smith, A. L. (2020). Stress signaling in teleost fish. Journal of Comparative Physiology, 210(4), 345-360."
    result = validate_stage08_pp01_fixture(
        text=body,
        full_text="",
        abstract_text="This abstract describes conserved stress signaling in teleosts.",
    )

    assert result["status"] == "pass"
    assert result["evidence_items"][0]["selection"] == "abstract"
    assert result["evidence_items"][0]["source"] == "abstract"
    assert result["evidence_items"][0]["fallback_used"] is True
