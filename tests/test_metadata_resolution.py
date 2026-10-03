from unittest.mock import patch

from src.metadata.extraction import DeclaredReference, Author
from src.metadata.crossref_metadata import ActualReference
from src.metadata.resolution import resolve_reference, BLOCKING_STATUSES

TEST_EMAIL = "test@example.com"


def _declared(**overrides):
    base = dict(
        reference_id=1,
        raw="raw text",
        authors=[Author(family="Lien", given="S.")],
        year=2016,
        title="The Atlantic salmon genome provides insights into rediploidization",
        journal="Nature",
        volume="533",
        issue="7602",
        pages="200-205",
        doi="10.1038/nature17164",
    )
    base.update(overrides)
    return DeclaredReference(**base)


def _actual(**overrides):
    base = dict(
        doi="10.1038/nature17164",
        status="resolved",
        title="The Atlantic salmon genome provides insights into rediploidization",
        authors=[Author(family="Lien", given="S.")],
        year=2016,
        journal="Nature",
        volume="533",
        issue="7602",
        pages="200-205",
        issn="0028-0836",
    )
    base.update(overrides)
    return ActualReference(**base)


# ---------------------------------------------------------------------------
# Route: DOI present and resolves, title matches -> proceed to field comparison
# ---------------------------------------------------------------------------

@patch("src.metadata.resolution.fetch_by_doi")
def test_doi_resolves_and_matches_proceeds_to_field_comparison(mock_fetch):
    mock_fetch.return_value = _actual()
    result = resolve_reference(_declared(), contact_email=TEST_EMAIL)

    assert result.status == "_needs_field_comparison"
    assert result.blocks_phase_2 is False
    assert result.actual.doi == "10.1038/nature17164"
    assert result.usable_identifier == "10.1038/nature17164"


# ---------------------------------------------------------------------------
# Route: no DOI at all -> title+author search
# ---------------------------------------------------------------------------

@patch("src.metadata.resolution.search_by_title_author")
def test_no_doi_resolves_by_title_author_and_matches(mock_search):
    mock_search.return_value = _actual(title_match_score=0.97)
    result = resolve_reference(_declared(doi=None), contact_email=TEST_EMAIL)

    assert result.status == "_needs_field_comparison"
    assert result.blocks_phase_2 is False
    mock_search.assert_called_once()


@patch("src.metadata.resolution.search_by_title_author")
def test_no_doi_and_search_finds_nothing_is_not_found_and_blocks(mock_search):
    mock_search.return_value = ActualReference(doi="", status="not_found")
    result = resolve_reference(_declared(doi=None), contact_email=TEST_EMAIL)

    assert result.status == "not_found"
    assert result.blocks_phase_2 is True
    assert result.status in BLOCKING_STATUSES


@patch("src.metadata.resolution.search_by_title_author")
def test_no_doi_and_search_finds_weak_match_is_synthesized_title_not_blocking(mock_search):
    mock_search.return_value = _actual(title_match_score=0.4)
    result = resolve_reference(_declared(doi=None), contact_email=TEST_EMAIL)

    assert result.status == "synthesized_title"
    assert result.blocks_phase_2 is False


# ---------------------------------------------------------------------------
# Route: DOI present but doesn't resolve -> falls through to title+author search
# ---------------------------------------------------------------------------

@patch("src.metadata.resolution.search_by_title_author")
@patch("src.metadata.resolution.fetch_by_doi")
def test_doi_not_found_falls_through_to_title_author_search(mock_fetch, mock_search):
    mock_fetch.return_value = ActualReference(doi="10.1038/nature17164", status="not_found")
    mock_search.return_value = _actual(title_match_score=0.95)

    result = resolve_reference(_declared(), contact_email=TEST_EMAIL)

    assert result.status == "_needs_field_comparison"
    mock_search.assert_called_once()


@patch("src.metadata.resolution.search_by_title_author")
@patch("src.metadata.resolution.fetch_by_doi")
def test_doi_not_found_and_search_also_not_found_is_not_found(mock_fetch, mock_search):
    mock_fetch.return_value = ActualReference(doi="10.1038/nature17164", status="not_found")
    mock_search.return_value = ActualReference(doi="", status="not_found")

    result = resolve_reference(_declared(), contact_email=TEST_EMAIL)

    assert result.status == "not_found"
    assert result.blocks_phase_2 is True


# ---------------------------------------------------------------------------
# Route: DOI resolves to a DIFFERENT article -> rescue flow
# ---------------------------------------------------------------------------

@patch("src.metadata.resolution.search_by_title_author")
@patch("src.metadata.resolution.fetch_by_doi")
def test_doi_resolves_to_wrong_article_rescue_finds_nothing_blocks(mock_fetch, mock_search):
    mock_fetch.return_value = _actual(title="A Completely Different Paper About Coral Reefs")
    mock_search.return_value = ActualReference(doi="", status="not_found")

    result = resolve_reference(_declared(), contact_email=TEST_EMAIL)

    assert result.status == "wrong_article"
    assert result.blocks_phase_2 is True
    assert result.status in BLOCKING_STATUSES


@patch("src.metadata.resolution.search_by_title_author")
@patch("src.metadata.resolution.fetch_by_doi")
def test_doi_resolves_to_wrong_article_rescue_finds_weak_match_is_synthesized_title(mock_fetch, mock_search):
    mock_fetch.return_value = _actual(title="A Completely Different Paper About Coral Reefs")
    mock_search.return_value = _actual(title_match_score=0.3)

    result = resolve_reference(_declared(), contact_email=TEST_EMAIL)

    assert result.status == "synthesized_title"
    assert result.blocks_phase_2 is False


@patch("src.metadata.resolution.search_by_title_author")
@patch("src.metadata.resolution.fetch_by_doi")
def test_doi_resolves_to_wrong_article_rescue_finds_strong_match_proceeds(mock_fetch, mock_search):
    mock_fetch.return_value = _actual(title="A Completely Different Paper About Coral Reefs")
    mock_search.return_value = _actual(title_match_score=0.95)

    result = resolve_reference(_declared(), contact_email=TEST_EMAIL)

    assert result.status == "_needs_field_comparison"
    assert result.blocks_phase_2 is False
    # The rescued record is what gets used going forward, not the wrong one fetch_by_doi returned.
    assert result.actual.title_match_score == 0.95


# ---------------------------------------------------------------------------
# Technical failures -- treated as blocking, distinct from not_found
# ---------------------------------------------------------------------------

@patch("src.metadata.resolution.fetch_by_doi")
def test_doi_fetch_error_is_technical_failure_not_not_found(mock_fetch):
    mock_fetch.return_value = ActualReference(doi="10.1038/nature17164", status="error", error_message="timeout")
    result = resolve_reference(_declared(), contact_email=TEST_EMAIL)

    assert result.status == "technical_failure"
    assert result.blocks_phase_2 is True
    assert result.status != "not_found"  # a real distinction the spec/pipeline relies on elsewhere


@patch("src.metadata.resolution.search_by_title_author")
def test_search_error_when_no_doi_is_technical_failure(mock_search):
    mock_search.return_value = ActualReference(doi="", status="error", error_message="timeout")
    result = resolve_reference(_declared(doi=None), contact_email=TEST_EMAIL)

    assert result.status == "technical_failure"
    assert result.blocks_phase_2 is True


# ---------------------------------------------------------------------------
# Edge case: no title at all to compare against (extraction failed badly)
# ---------------------------------------------------------------------------

@patch("src.metadata.resolution.search_by_title_author")
@patch("src.metadata.resolution.fetch_by_doi")
def test_declared_has_no_title_does_not_crash_routes_to_rescue(mock_fetch, mock_search):
    mock_fetch.return_value = _actual()  # DOI resolves fine
    mock_search.return_value = ActualReference(doi="", status="not_found")

    result = resolve_reference(_declared(title=None), contact_email=TEST_EMAIL)

    # No declared title means we can't confirm a match, so it must not be
    # silently treated as "matches" -- falls into the rescue path, which
    # here finds nothing, so it blocks.
    assert result.status == "wrong_article"
    assert result.blocks_phase_2 is True
