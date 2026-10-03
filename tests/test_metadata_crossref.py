import responses

from src.metadata.crossref_metadata import fetch_by_doi, search_by_title_author, title_similarity
from src.config import CROSSREF_BASE_URL

TEST_EMAIL = "test@example.com"


def _work_message(**overrides):
    base = {
        "DOI": "10.1038/nature17164",
        "title": ["The Atlantic salmon genome provides insights into rediploidization"],
        "author": [
            {"given": "S.", "family": "Lien"},
            {"given": "B. F.", "family": "Koop"},
        ],
        "container-title": ["Nature"],
        "volume": "533",
        "issue": "7602",
        "page": "200-205",
        "ISSN": ["0028-0836", "1476-4687"],
        "type": "journal-article",
        "published-print": {"date-parts": [[2016, 6, 8]]},
    }
    base.update(overrides)
    return base


@responses.activate
def test_fetch_by_doi_extracts_full_record():
    doi = "10.1038/nature17164"
    responses.add(responses.GET, f"{CROSSREF_BASE_URL}/{doi}", json={"message": _work_message()}, status=200)

    result = fetch_by_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "resolved"
    assert result.title == "The Atlantic salmon genome provides insights into rediploidization"
    assert result.authors[0].family == "Lien"
    assert result.authors[0].given == "S."
    assert result.journal == "Nature"
    assert result.volume == "533"
    assert result.issue == "7602"
    assert result.pages == "200-205"
    assert result.issn == "0028-0836"
    assert result.publication_type == "journal-article"
    assert result.year == 2016


@responses.activate
def test_fetch_by_doi_not_found_is_final_not_error():
    doi = "10.9999/does-not-exist"
    responses.add(responses.GET, f"{CROSSREF_BASE_URL}/{doi}", json={}, status=404)

    result = fetch_by_doi(doi, contact_email=TEST_EMAIL)
    assert result.status == "not_found"
    assert len(responses.calls) == 1  # not retried


@responses.activate
def test_fetch_by_doi_retries_on_5xx_then_succeeds():
    doi = "10.1038/nature17164"
    responses.add(responses.GET, f"{CROSSREF_BASE_URL}/{doi}", status=500)
    responses.add(responses.GET, f"{CROSSREF_BASE_URL}/{doi}", json={"message": _work_message()}, status=200)

    result = fetch_by_doi(doi, contact_email=TEST_EMAIL)
    assert result.status == "resolved"
    assert len(responses.calls) == 2


def test_title_similarity_identical_titles_scores_1():
    assert title_similarity("The Atlantic Salmon Genome", "the atlantic salmon genome") == 1.0


def test_title_similarity_unrelated_titles_scores_low():
    assert title_similarity("The Atlantic Salmon Genome", "A Study of Coral Reef Fish") < 0.5


@responses.activate
def test_search_by_title_author_returns_score_alongside_high_similarity_match():
    responses.add(
        responses.GET,
        CROSSREF_BASE_URL,
        json={"message": {"items": [_work_message()]}},
        status=200,
    )

    result = search_by_title_author(
        "The Atlantic salmon genome provides insights into rediploidization",
        "Lien",
        contact_email=TEST_EMAIL,
    )
    assert result.status == "resolved"
    assert result.doi == "10.1038/nature17164"
    assert result.title_match_score > 0.85


@responses.activate
def test_search_by_title_author_returns_weak_candidate_with_low_score_not_not_found():
    """
    A weak match is still 'resolved' with a low score attached -- collapsing
    it to not_found here would make resolution.py unable to distinguish
    "found nothing" (blocks phase 2) from "found something, but it's a weak
    match" (synthesized_title, does NOT block) per the spec's flowchart.
    """
    responses.add(
        responses.GET,
        CROSSREF_BASE_URL,
        json={"message": {"items": [_work_message(title=["A Completely Unrelated Paper About Coral Reefs"])]}},
        status=200,
    )

    result = search_by_title_author(
        "The Atlantic salmon genome provides insights into rediploidization",
        "Lien",
        contact_email=TEST_EMAIL,
    )
    assert result.status == "resolved"
    assert result.title_match_score < 0.5


@responses.activate
def test_search_by_title_author_no_results_is_not_found():
    responses.add(responses.GET, CROSSREF_BASE_URL, json={"message": {"items": []}}, status=200)

    result = search_by_title_author("Some title", "Author", contact_email=TEST_EMAIL)
    assert result.status == "not_found"


def test_search_by_title_author_no_title_short_circuits_without_a_call():
    result = search_by_title_author("", "Author", contact_email=TEST_EMAIL)
    assert result.status == "not_found"
