from src.metadata.document import MetadataDoc
from src.metadata.extraction import DeclaredReference, Author
from src.metadata.crossref_metadata import ActualReference
from src.metadata.checks.phase_c import check_08, check_14_author_omission


def _ref(reference_id, **overrides):
    base = dict(reference_id=reference_id, raw="raw", authors=[], year=None, title=None,
                journal=None, volume=None, issue=None, pages=None, doi=None, issn=None)
    base.update(overrides)
    return DeclaredReference(**base)


def _actual(**overrides):
    base = dict(doi="10.1/x", status="resolved", title=None, authors=[], year=None,
                journal=None, volume=None, issue=None, pages=None, issn=None)
    base.update(overrides)
    return ActualReference(**base)


def _doc():
    return MetadataDoc(sentences=[], body_text="")


# ---------------------------------------------------------------------------
# check_08 -- displaced authorship (O(n^2) across the bibliography)
# ---------------------------------------------------------------------------

def test_check_08_flags_author_found_in_a_different_references_actual_list():
    ref1 = _ref(1, authors=[Author(family="Smith", given="J.")])
    ref2 = _ref(2, authors=[Author(family="Jones", given="A.")])
    records = {
        1: _actual(authors=[Author(family="Smith", given="J.")]),  # correctly its own
        2: _actual(authors=[Author(family="Smith", given="J.")]),  # Smith shows up here too -- displaced
    }
    findings = check_08(_doc(), [ref1, ref2], records)
    # ref1's declared author "Smith, J." also appears in ref2's actual list (not ref1's own)
    assert any(f.reference_id == 1 for f in findings)


def test_check_08_no_cross_contamination_is_silent():
    ref1 = _ref(1, authors=[Author(family="Smith", given="J.")])
    ref2 = _ref(2, authors=[Author(family="Jones", given="A.")])
    records = {
        1: _actual(authors=[Author(family="Smith", given="J.")]),
        2: _actual(authors=[Author(family="Jones", given="A.")]),
    }
    assert check_08(_doc(), [ref1, ref2], records) == []


def test_check_08_does_not_flag_authors_matching_their_own_reference():
    ref1 = _ref(1, authors=[Author(family="Smith", given="J.")])
    records = {1: _actual(authors=[Author(family="Smith", given="J.")])}
    assert check_08(_doc(), [ref1], records) == []


def test_check_08_requires_both_surname_and_initial_to_match():
    ref1 = _ref(1, authors=[Author(family="Smith", given="J.")])
    ref2 = _ref(2, authors=[Author(family="Jones", given="A.")])
    records = {
        1: _actual(authors=[Author(family="Smith", given="J.")]),
        2: _actual(authors=[Author(family="Smith", given="K.")]),  # same surname, different initial
    }
    assert check_08(_doc(), [ref1, ref2], records) == []


def test_check_08_names_both_references_involved():
    ref1 = _ref(1, authors=[Author(family="Smith", given="J.")])
    ref2 = _ref(2, authors=[])
    records = {
        1: _actual(authors=[]),
        2: _actual(authors=[Author(family="Smith", given="J.")]),
    }
    findings = check_08(_doc(), [ref1, ref2], records)
    assert len(findings) == 1
    assert findings[0].reference_id == 1
    assert "[2]" in findings[0].message


def test_check_08_skips_unresolved_references():
    ref1 = _ref(1, authors=[Author(family="Smith", given="J.")])
    ref2 = _ref(2, authors=[])
    records = {1: _actual(authors=[]), 2: _actual(status="not_found")}
    assert check_08(_doc(), [ref1, ref2], records) == []


# ---------------------------------------------------------------------------
# check_14_author_omission -- substantive half, colocated with Phase C
# ---------------------------------------------------------------------------

def test_check_14_omission_flags_fewer_declared_than_actual_without_marker():
    ref = _ref(1, authors=[Author(family="Smith", given="J.")], authors_truncated=False)
    actual = _actual(authors=[Author(family="Smith", given="J."), Author(family="Jones", given="A.")])
    findings = check_14_author_omission(_doc(), [ref], {1: actual})
    assert len(findings) == 1
    assert findings[0].declared == "1"
    assert findings[0].actual == "2"


def test_check_14_omission_silent_when_truncation_marker_present():
    ref = _ref(1, authors=[Author(family="Smith", given="J.")], authors_truncated=True)
    actual = _actual(authors=[Author(family="Smith", given="J."), Author(family="Jones", given="A.")])
    assert check_14_author_omission(_doc(), [ref], {1: actual}) == []


def test_check_14_omission_silent_when_counts_match():
    ref = _ref(1, authors=[Author(family="Smith", given="J.")], authors_truncated=False)
    actual = _actual(authors=[Author(family="Smith", given="J.")])
    assert check_14_author_omission(_doc(), [ref], {1: actual}) == []


def test_check_14_omission_silent_when_declared_has_zero_authors():
    # Nothing to compare meaningfully -- extraction likely failed, not a real omission signal
    ref = _ref(1, authors=[], authors_truncated=False)
    actual = _actual(authors=[Author(family="Smith", given="J.")])
    assert check_14_author_omission(_doc(), [ref], {1: actual}) == []
