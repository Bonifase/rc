from unittest.mock import patch

import pytest

from src.metadata.checks.phase_b import (check_07, check_09, check_10,
                                         check_11, check_15, check_16)
from src.metadata.crossref_metadata import ActualReference
from src.metadata.document import MetadataDoc
from src.metadata.extraction import Author, DeclaredReference


def _ref(reference_id, **overrides):
    base = dict(reference_id=reference_id, raw="raw", authors=[], year=None, title=None,
                journal=None, volume=None, issue=None, pages=None, doi=None, issn=None)
    base.update(overrides)
    return DeclaredReference(**base)


def _actual(**overrides):
    base = dict(doi="10.1/x", status="resolved", title=None, authors=[], year=None,
                journal=None, volume=None, issue=None, pages=None, issn=None,
                retracted=False, retraction_notice=None, retraction_source=None)
    base.update(overrides)
    return ActualReference(**base)


def _doc():
    return MetadataDoc(sentences=[], body_text="")


# ---------------------------------------------------------------------------
# check_07 -- quiet error: title matches, author doesn't
# ---------------------------------------------------------------------------

def test_check_07_flags_matching_title_but_mismatched_author():
    ref = _ref(1, title="The Atlantic salmon genome provides insights", authors=[Author(family="Lien", given="S.")])
    actual = _actual(title="The Atlantic salmon genome provides insights", authors=[Author(family="Smith", given="J.")])
    findings = check_07(_doc(), [ref], {1: actual})
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert findings[0].declared == "lien"


def test_check_07_matching_title_and_author_is_silent():
    ref = _ref(1, title="The Atlantic salmon genome provides insights", authors=[Author(family="Lien", given="S.")])
    actual = _actual(title="The Atlantic salmon genome provides insights", authors=[Author(family="Lien", given="S.")])
    assert check_07(_doc(), [ref], {1: actual}) == []


def test_check_07_skips_unresolved_references():
    ref = _ref(1, title="Some title", authors=[Author(family="X", given="Y")])
    assert check_07(_doc(), [ref], {1: _actual(status="not_found")}) == []


def test_check_07_skips_when_title_similarity_already_low():
    # Not this check's job -- resolution.py already routes this to wrong_article/synthesized_title
    ref = _ref(1, title="Completely different title entirely", authors=[Author(family="Lien", given="S.")])
    actual = _actual(title="The Atlantic salmon genome provides insights", authors=[Author(family="Smith", given="J.")])
    assert check_07(_doc(), [ref], {1: actual}) == []


# ---------------------------------------------------------------------------
# check_09 -- synthesized (non-verbatim) title
# ---------------------------------------------------------------------------

def test_check_09_flags_close_but_not_verbatim_title():
    ref = _ref(1, title="The Atlantic salmon genome gives insight into rediploidization")
    actual = _actual(title="The Atlantic salmon genome provides insights into rediploidization")
    findings = check_09(_doc(), [ref], {1: actual})
    assert len(findings) == 1
    assert findings[0].field == "title"


def test_check_09_verbatim_title_is_silent():
    ref = _ref(1, title="The Atlantic salmon genome provides insights into rediploidization")
    actual = _actual(title="The Atlantic salmon genome provides insights into rediploidization")
    assert check_09(_doc(), [ref], {1: actual}) == []


def test_check_09_very_low_similarity_not_this_checks_job():
    # Below 0.85 -- resolution.py's own routing already handles this case
    ref = _ref(1, title="A totally unrelated title")
    actual = _actual(title="The Atlantic salmon genome provides insights into rediploidization")
    assert check_09(_doc(), [ref], {1: actual}) == []


# ---------------------------------------------------------------------------
# check_10 -- journal / volume / pages substitution
# ---------------------------------------------------------------------------

def test_check_10_flags_journal_substitution():
    ref = _ref(1, journal="Communications Biology")
    actual = _actual(journal="Scientific Reports")
    findings = check_10(_doc(), [ref], {1: actual})
    assert any(f.field == "journal" and f.severity == "critical" for f in findings)


def test_check_10_flags_volume_mismatch():
    ref = _ref(1, volume="5")
    actual = _actual(volume="7")
    findings = check_10(_doc(), [ref], {1: actual})
    assert any(f.field == "volume" for f in findings)


def test_check_10_flags_pages_mismatch():
    ref = _ref(1, pages="100-110")
    actual = _actual(pages="200-210")
    findings = check_10(_doc(), [ref], {1: actual})
    assert any(f.field == "pages" for f in findings)


def test_check_10_all_fields_match_is_silent():
    ref = _ref(1, journal="Nature", volume="533", pages="200-205")
    actual = _actual(journal="Nature", volume="533", pages="200-205")
    assert check_10(_doc(), [ref], {1: actual}) == []


def test_check_10_tolerates_minor_journal_formatting_differences():
    ref = _ref(1, journal="Nature")
    actual = _actual(journal="nature ")
    assert check_10(_doc(), [ref], {1: actual}) == []


@pytest.mark.parametrize("declared,actual", [
    ("1841-1851", "1841\u20131851"),
    ("1841\u20141851", "1841-1851"),
    ("1841 - 1851", "1841-1851"),
    ("1841-51", "1841-1851"),
])
def test_check_10_ignores_dash_style_and_abbreviated_page_ranges(declared, actual):
    assert check_10(_doc(), [_ref(1, pages=declared)], {1: _actual(pages=actual)}) == []


def test_check_10_still_flags_a_real_page_difference_across_dash_styles():
    findings = check_10(_doc(), [_ref(1, pages="1841-1851")], {1: _actual(pages="1842\u20131851")})
    assert [f.field for f in findings] == ["pages"]


# ---------------------------------------------------------------------------
# check_16 -- declared-vs-actual year (added per client request)
# ---------------------------------------------------------------------------

def test_check_16_flags_declared_vs_actual_year_mismatch():
    ref = _ref(1, year=2022)
    actual = _actual(year=2009)
    findings = check_16(_doc(), [ref], {1: actual})
    assert len(findings) == 1
    assert findings[0].check_id == 16
    assert findings[0].field == "year"
    assert findings[0].declared == "2022"
    assert findings[0].actual == "2009"


def test_check_16_matching_year_is_silent():
    ref = _ref(1, year=2010)
    actual = _actual(year=2010)
    assert check_16(_doc(), [ref], {1: actual}) == []


def test_check_16_missing_declared_year_is_cannot_compare_not_mismatch():
    # No parseable declared year -- "cannot compare", must NOT be flagged.
    ref = _ref(1, year=None)
    actual = _actual(year=2010)
    assert check_16(_doc(), [ref], {1: actual}) == []


def test_check_16_record_without_year_is_cannot_compare_not_mismatch():
    ref = _ref(1, year=2010)
    actual = _actual(year=None)
    assert check_16(_doc(), [ref], {1: actual}) == []


def test_check_16_skips_unresolved_or_absent_records():
    ref = _ref(1, year=2010)
    assert check_16(_doc(), [ref], {1: _actual(status="not_found", year=2010)}) == []
    assert check_16(_doc(), [ref], {}) == []


def test_check_16_multiple_references_reported_individually():
    refs = [_ref(1, year=2020), _ref(2, year=2009)]
    records = {1: _actual(year=2021), 2: _actual(year=2009)}
    findings = check_16(_doc(), refs, records)
    assert [f.reference_id for f in findings] == [1]


# ---------------------------------------------------------------------------
# check_15 -- retracted paper (client-requested; free Crossref RW data)
# ---------------------------------------------------------------------------

def test_check_15_flags_retracted_record():
    ref = _ref(1)
    actual = _actual(doi="10.1155/2021/5554487", retracted=True,
                     retraction_notice="10.1155/2024/9753878",
                     retraction_source="retraction-watch")
    findings = check_15(_doc(), [ref], {1: actual})
    assert len(findings) == 1
    assert findings[0].check_id == 15
    assert findings[0].severity == "critical"
    assert findings[0].field == "retraction"
    assert findings[0].actual == "10.1155/2024/9753878"


def test_check_15_non_retracted_record_is_silent():
    ref = _ref(1)
    actual = _actual(doi="10.1016/j.ygcen.2010.01.023", retracted=False)
    assert check_15(_doc(), [ref], {1: actual}) == []


def test_check_15_retracted_flag_defaults_to_false():
    # A record parsed WITHOUT any retraction marker must default to not retracted.
    ref = _ref(1)
    actual = _actual()  # retracted not passed -- defaults False
    assert check_15(_doc(), [ref], {1: actual}) == []


def test_check_15_skips_unresolved_or_absent_records():
    ref = _ref(1)
    assert check_15(_doc(), [ref], {1: _actual(status="not_found", retracted=True)}) == []
    assert check_15(_doc(), [ref], {}) == []


def test_check_15_retraction_message_carries_notice_and_source():
    ref = _ref(1)
    actual = _actual(retracted=True, retraction_notice="10.1155/2024/9753878", retraction_source="publisher")
    findings = check_15(_doc(), [ref], {1: actual})
    assert "retraction notice (10.1155/2024/9753878)" in findings[0].message
    assert "registered by publisher" in findings[0].message


# ---------------------------------------------------------------------------
# check_11 -- volume within the journal's published range (via ISSN sample)
# ---------------------------------------------------------------------------

@patch("src.metadata.checks.phase_b.fetch_journal_volume_samples")
def test_check_11_flags_volume_outside_expected_range(mock_fetch):
    mock_fetch.return_value = [(2010, "500"), (2020, "560")]
    ref = _ref(1, year=2015, volume="10")  # way outside 500-560
    actual = _actual(issn="1234-5678")
    findings = check_11(_doc(), [ref], {1: actual}, contact_email="test@example.com")
    assert len(findings) == 1
    assert findings[0].declared == "10"


@patch("src.metadata.checks.phase_b.fetch_journal_volume_samples")
def test_check_11_volume_within_expected_range_is_silent(mock_fetch):
    mock_fetch.return_value = [(2010, "500"), (2020, "560")]
    ref = _ref(1, year=2015, volume="530")
    actual = _actual(issn="1234-5678")
    assert check_11(_doc(), [ref], {1: actual}, contact_email="test@example.com") == []


@patch("src.metadata.checks.phase_b.fetch_journal_volume_samples")
def test_check_11_abstains_when_declared_year_outside_sample_range(mock_fetch):
    mock_fetch.return_value = [(2010, "500"), (2020, "560")]
    ref = _ref(1, year=1990, volume="1")  # year far outside the sample -- must not extrapolate
    actual = _actual(issn="1234-5678")
    assert check_11(_doc(), [ref], {1: actual}, contact_email="test@example.com") == []


@patch("src.metadata.checks.phase_b.fetch_journal_volume_samples")
def test_check_11_caches_journal_samples_across_references_sharing_an_issn(mock_fetch):
    mock_fetch.return_value = [(2010, "500"), (2020, "560")]
    refs = [
        _ref(1, year=2015, volume="530"),
        _ref(2, year=2016, volume="540"),
    ]
    records = {1: _actual(issn="1234-5678"), 2: _actual(issn="1234-5678")}
    check_11(_doc(), refs, records, contact_email="test@example.com")
    mock_fetch.assert_called_once()  # not once per reference


@patch("src.metadata.checks.phase_b.fetch_journal_volume_samples")
def test_check_11_abstains_with_insufficient_sample_data(mock_fetch):
    mock_fetch.return_value = [(2015, "530")]  # only one sample point
    ref = _ref(1, year=2015, volume="1")
    actual = _actual(issn="1234-5678")
    assert check_11(_doc(), [ref], {1: actual}, contact_email="test@example.com") == []
