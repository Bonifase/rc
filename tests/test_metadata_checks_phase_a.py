from src.metadata.document import MetadataDoc
from src.metadata.extraction import DeclaredReference, Author
from src.metadata.checks.phase_a import check_01, check_02, check_03, check_04, check_05, check_13, check_14


def _ref(reference_id, **overrides):
    base = dict(reference_id=reference_id, raw="raw", authors=[], year=None, title=None,
                journal=None, volume=None, issue=None, pages=None, doi=None, issn=None)
    base.update(overrides)
    return DeclaredReference(**base)


def _doc(sentences=None, body_text=""):
    return MetadataDoc(sentences=sentences or [], body_text=body_text)


# ---------------------------------------------------------------------------
# check_01 -- repurposed: duplicate DOI across different reference numbers
# ---------------------------------------------------------------------------

def test_check_01_flags_two_reference_numbers_sharing_a_doi():
    refs = [_ref(1, doi="10.1/x"), _ref(2, doi="10.1/x"), _ref(3, doi="10.1/y")]
    findings = check_01(_doc(), refs, {})
    assert len(findings) == 2
    assert {f.reference_id for f in findings} == {1, 2}
    assert all(f.check_id == 1 for f in findings)


def test_check_01_no_duplicates_no_findings():
    refs = [_ref(1, doi="10.1/x"), _ref(2, doi="10.1/y")]
    assert check_01(_doc(), refs, {}) == []


def test_check_01_ignores_references_with_no_doi():
    refs = [_ref(1, doi=None), _ref(2, doi=None)]
    assert check_01(_doc(), refs, {}) == []


# ---------------------------------------------------------------------------
# check_02 -- partition coherence (stated count vs actual bracket group size)
# ---------------------------------------------------------------------------

def test_check_02_flags_mismatched_number_word():
    doc = _doc(sentences=[{"id": "s1", "text": "All five studies [1-4] agree."}])
    findings = check_02(doc, [], {})
    assert len(findings) == 1
    assert findings[0].declared == "5"
    assert findings[0].actual == "4"


def test_check_02_matching_count_is_silent():
    doc = _doc(sentences=[{"id": "s1", "text": "All four studies [1-4] agree."}])
    assert check_02(doc, [], {}) == []


def test_check_02_digit_count_also_checked():
    doc = _doc(sentences=[{"id": "s1", "text": "In 3 papers [1,2] this was shown."}])
    findings = check_02(doc, [], {})
    assert len(findings) == 1
    assert findings[0].declared == "3"
    assert findings[0].actual == "2"


def test_check_02_no_preceding_number_is_silent():
    doc = _doc(sentences=[{"id": "s1", "text": "This was shown [1,2]."}])
    assert check_02(doc, [], {}) == []


def test_check_02_does_not_grab_unrelated_distant_number_like_a_year():
    """
    Regression test: an earlier fix scanned arbitrarily far back through the
    preceding text for any number-like token, which could grab an unrelated
    number (e.g. a year) instead of the actual stated count, or lack of one.
    """
    doc = _doc(sentences=[{"id": "s1", "text": "The 2020 study [1,2] confirmed this."}])
    assert check_02(doc, [], {}) == []


# ---------------------------------------------------------------------------
# check_03 -- declared reference count vs bibliography length
# ---------------------------------------------------------------------------

def test_check_03_flags_mismatch():
    doc = _doc(body_text="This review included 10 studies on the topic.")
    refs = [_ref(i) for i in range(1, 8)]  # only 7 listed
    findings = check_03(doc, refs, {})
    assert len(findings) == 1
    assert findings[0].declared == "10"
    assert findings[0].actual == "7"


def test_check_03_matching_count_is_silent():
    doc = _doc(body_text="This review included 7 studies on the topic.")
    refs = [_ref(i) for i in range(1, 8)]
    assert check_03(doc, refs, {}) == []


def test_check_03_abstains_when_no_declared_count_phrase():
    doc = _doc(body_text="This is a review with no explicit count statement.")
    refs = [_ref(1)]
    assert check_03(doc, refs, {}) == []


def test_check_03_abstains_when_multiple_ambiguous_phrases():
    doc = _doc(body_text="We included 10 studies. A related review found 15 articles.")
    refs = [_ref(i) for i in range(1, 8)]
    assert check_03(doc, refs, {}) == []


# ---------------------------------------------------------------------------
# check_04 -- same reference cited with different percentages
# ---------------------------------------------------------------------------

def test_check_04_flags_different_percentages_for_same_reference():
    doc = _doc(sentences=[
        {"id": "s1", "text": "Reference reports 25% of cases [6]."},
        {"id": "s2", "text": "The same source found 30% overall [6]."},
    ])
    findings = check_04(doc, [], {})
    assert len(findings) == 1
    assert findings[0].reference_id == 6
    assert "25" in findings[0].declared
    assert "30" in findings[0].actual


def test_check_04_same_percentage_repeated_is_silent():
    doc = _doc(sentences=[
        {"id": "s1", "text": "Reference reports 25% of cases [6]."},
        {"id": "s2", "text": "The same source confirms 25% overall [6]."},
    ])
    assert check_04(doc, [], {}) == []


def test_check_04_different_references_not_compared_to_each_other():
    doc = _doc(sentences=[
        {"id": "s1", "text": "Study found 25% [1]."},
        {"id": "s2", "text": "Another study found 60% [2]."},
    ])
    assert check_04(doc, [], {}) == []


# ---------------------------------------------------------------------------
# check_05 -- year-volume coherence within the declared bibliography
# ---------------------------------------------------------------------------

def test_check_05_flags_later_year_with_lower_volume_same_journal():
    refs = [
        _ref(1, journal="Nature", year=2015, volume="520"),
        _ref(2, journal="Nature", year=2018, volume="500"),  # should be higher, not lower
    ]
    findings = check_05(_doc(), refs, {})
    assert len(findings) == 1
    assert findings[0].reference_id == 2


def test_check_05_consistent_progression_is_silent():
    refs = [
        _ref(1, journal="Nature", year=2015, volume="520"),
        _ref(2, journal="Nature", year=2018, volume="560"),
    ]
    assert check_05(_doc(), refs, {}) == []


def test_check_05_different_journals_not_compared():
    refs = [
        _ref(1, journal="Nature", year=2018, volume="560"),
        _ref(2, journal="Science", year=2015, volume="10"),
    ]
    assert check_05(_doc(), refs, {}) == []


def test_check_05_single_entry_per_journal_is_silent():
    refs = [_ref(1, journal="Nature", year=2018, volume="560")]
    assert check_05(_doc(), refs, {}) == []


# ---------------------------------------------------------------------------
# check_13 -- citation markers vs bibliography, both directions
# ---------------------------------------------------------------------------

def test_check_13_flags_orphaned_marker():
    doc = _doc(sentences=[{"id": "s1", "text": "A claim citing a missing ref [9]."}])
    refs = [_ref(1)]
    findings = check_13(doc, refs, {})
    assert any(f.declared == "9" and f.severity == "critical" for f in findings)


def test_check_13_flags_never_cited_reference():
    doc = _doc(sentences=[{"id": "s1", "text": "A claim [1]."}])
    refs = [_ref(1), _ref(2)]
    findings = check_13(doc, refs, {})
    assert any(f.reference_id == 2 and f.severity == "warning" for f in findings)


def test_check_13_fully_consistent_bibliography_is_silent():
    doc = _doc(sentences=[{"id": "s1", "text": "A claim [1] and another [2]."}])
    refs = [_ref(1), _ref(2)]
    assert check_13(doc, refs, {}) == []


# ---------------------------------------------------------------------------
# check_14 -- rule-based half only (malformed truncation marker)
# ---------------------------------------------------------------------------

def test_check_14_flags_truncation_marker_with_zero_named_authors():
    refs = [_ref(1, authors=[], authors_truncated=True, raw="et al. (2020). A title. Journal, 1, 1-2.")]
    findings = check_14(_doc(), refs, {})
    assert len(findings) == 1
    assert findings[0].reference_id == 1


def test_check_14_truncation_marker_with_named_authors_is_silent():
    refs = [_ref(1, authors=[Author(family="Smith", given="J.")], authors_truncated=True)]
    assert check_14(_doc(), refs, {}) == []


def test_check_14_no_truncation_marker_is_silent_regardless_of_author_count():
    refs = [_ref(1, authors=[], authors_truncated=False)]
    assert check_14(_doc(), refs, {}) == []
