from unittest.mock import patch

from src.metadata.crossref_metadata import ActualReference
from src.metadata.document import MetadataDoc
from src.metadata.extraction import Author, DeclaredReference
from src.metadata.report import build_report
from src.metadata.resolution import ResolvedReference

TEST_EMAIL = "test@example.com"


def _ref(reference_id, **overrides):
    base = dict(reference_id=reference_id, raw="raw", authors=[], year=None, title=None,
                journal=None, volume=None, issue=None, pages=None, doi=f"10.1/{reference_id}", issn=None)
    base.update(overrides)
    return DeclaredReference(**base)


def _resolved_ok(reference_id, actual=None):
    return ResolvedReference(
        reference_id=reference_id,
        status="_needs_field_comparison",
        actual=actual or ActualReference(doi=f"10.1/{reference_id}", status="resolved"),
        usable_identifier=f"10.1/{reference_id}",
        blocks_phase_2=False,
    )


# ---------------------------------------------------------------------------
# Acceptance criterion: a reference used by five sentences produces ONE
# finding/entry, not five.
# ---------------------------------------------------------------------------

@patch("src.metadata.report.resolve_reference")
def test_reference_cited_by_five_sentences_appears_once(mock_resolve):
    mock_resolve.return_value = _resolved_ok(1, ActualReference(
        doi="10.1/1", status="resolved", journal="Nature", volume="500",
    ))
    ref = _ref(1, journal="Different Journal Entirely")  # will trigger a check_10 finding
    doc = MetadataDoc(sentences=[
        {"id": f"s{i}", "text": f"A claim citing the source [1]."} for i in range(5)
    ])

    report = build_report(doc, [ref], contact_email=TEST_EMAIL)

    assert len(report["bibliography_status"]) == 1
    entry = report["bibliography_status"][0]
    assert entry["reference_id"] == 1
    assert set(entry["used_in_sentences"]) == {"s0", "s1", "s2", "s3", "s4"}
    # The journal-mismatch finding appears once on the reference, not once per sentence.
    assert len([f for f in entry["findings"] if f["field"] == "journal"]) == 1


# ---------------------------------------------------------------------------
# Acceptance criterion: miscited NEVER blocks phase 2.
# ---------------------------------------------------------------------------

@patch("src.metadata.report.resolve_reference")
def test_miscited_reference_does_not_block_phase_2(mock_resolve):
    mock_resolve.return_value = _resolved_ok(1, ActualReference(
        doi="10.1/1", status="resolved", journal="Real Journal", volume="500",
    ))
    ref = _ref(1, journal="Wrong Journal Name")
    doc = MetadataDoc(sentences=[{"id": "s1", "text": "A claim [1]."}])

    report = build_report(doc, [ref], contact_email=TEST_EMAIL)
    entry = report["bibliography_status"][0]

    assert entry["status"] == "miscited"
    assert entry["blocks_phase_2"] is False


# ---------------------------------------------------------------------------
# well_cited: matched, no field-comparison findings fired.
# ---------------------------------------------------------------------------

@patch("src.metadata.report.resolve_reference")
def test_matched_reference_with_no_findings_is_well_cited(mock_resolve):
    mock_resolve.return_value = _resolved_ok(1, ActualReference(
        doi="10.1/1", status="resolved", journal="Nature", volume="500", pages="1-10",
        title="Exact Title", authors=[Author(family="Smith", given="J.")],
    ))
    ref = _ref(1, journal="Nature", volume="500", pages="1-10", title="Exact Title",
               authors=[Author(family="Smith", given="J.")])
    doc = MetadataDoc(sentences=[{"id": "s1", "text": "A claim [1]."}])

    report = build_report(doc, [ref], contact_email=TEST_EMAIL)
    entry = report["bibliography_status"][0]

    assert entry["status"] == "well_cited"
    assert entry["blocks_phase_2"] is False
    assert entry["findings"] == []


# ---------------------------------------------------------------------------
# Blocking statuses pass through unchanged and DO block.
# ---------------------------------------------------------------------------

@patch("src.metadata.report.resolve_reference")
def test_not_found_blocks_phase_2(mock_resolve):
    mock_resolve.return_value = ResolvedReference(reference_id=1, status="not_found", blocks_phase_2=True)
    ref = _ref(1)
    doc = MetadataDoc(sentences=[{"id": "s1", "text": "A claim [1]."}])

    report = build_report(doc, [ref], contact_email=TEST_EMAIL)
    entry = report["bibliography_status"][0]

    assert entry["status"] == "not_found"
    assert entry["blocks_phase_2"] is True


@patch("src.metadata.report.resolve_reference")
def test_wrong_article_blocks_phase_2(mock_resolve):
    mock_resolve.return_value = ResolvedReference(reference_id=1, status="wrong_article", blocks_phase_2=True)
    ref = _ref(1)
    doc = MetadataDoc(sentences=[{"id": "s1", "text": "A claim [1]."}])

    report = build_report(doc, [ref], contact_email=TEST_EMAIL)
    assert report["bibliography_status"][0]["status"] == "wrong_article"
    assert report["bibliography_status"][0]["blocks_phase_2"] is True


@patch("src.metadata.report.resolve_reference")
def test_synthesized_title_does_not_block(mock_resolve):
    mock_resolve.return_value = ResolvedReference(
        reference_id=1, status="synthesized_title",
        actual=ActualReference(doi="10.1/1", status="resolved"), blocks_phase_2=False,
    )
    ref = _ref(1)
    doc = MetadataDoc(sentences=[{"id": "s1", "text": "A claim [1]."}])

    report = build_report(doc, [ref], contact_email=TEST_EMAIL)
    assert report["bibliography_status"][0]["status"] == "synthesized_title"
    assert report["bibliography_status"][0]["blocks_phase_2"] is False


# ---------------------------------------------------------------------------
# Document-level findings (no single reference_id) are separated out.
# ---------------------------------------------------------------------------

@patch("src.metadata.report.resolve_reference")
def test_orphaned_citation_marker_is_a_document_level_finding(mock_resolve):
    mock_resolve.return_value = _resolved_ok(1)
    ref = _ref(1)
    doc = MetadataDoc(sentences=[{"id": "s1", "text": "A claim citing missing ref [9]."}])

    report = build_report(doc, [ref], contact_email=TEST_EMAIL)

    assert any(f["field"] == "citation_marker" and f["declared"] == "9" for f in report["document_level_findings"])
    # Should not appear duplicated inside any reference's own findings list
    for entry in report["bibliography_status"]:
        assert all(f.get("declared") != "9" for f in entry["findings"])


# ---------------------------------------------------------------------------
# The un-implemented semantic half of check 14 is visible, not silently dropped.
# ---------------------------------------------------------------------------

@patch("src.metadata.report.resolve_reference")
def test_not_evaluated_checks_always_present(mock_resolve):
    mock_resolve.return_value = _resolved_ok(1)
    ref = _ref(1)
    doc = MetadataDoc(sentences=[{"id": "s1", "text": "A claim [1]."}])

    report = build_report(doc, [ref], contact_email=TEST_EMAIL)

    assert any(c["check_id"] == 14 and c["component"] == "screening_criterion" for c in report["not_evaluated_checks"])


@patch("src.metadata.report.resolve_reference")
def test_build_report_includes_official_records(mock_resolve):
    actual = ActualReference(
        doi="10.1/1",
        status="resolved",
        title="Exact Title",
        year=2020,
        journal="Nature",
        volume="5",
        pages="1-10",
        authors=[Author(family="Smith", given="J.")],
    )
    mock_resolve.return_value = _resolved_ok(1, actual)
    ref = _ref(1, title="Exact Title", year=2020, journal="Nature", volume="5", pages="1-10",
               authors=[Author(family="Smith", given="J.")])
    doc = MetadataDoc(sentences=[{"id": "s1", "text": "A claim [1]."}])

    report = build_report(doc, [ref], contact_email=TEST_EMAIL)

    assert "official_records" in report
    assert report["official_records"]["1"]["official_ref_text"] is not None
    assert report["official_records"]["1"]["verified_title_match"] is True
