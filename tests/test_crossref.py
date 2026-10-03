import responses
import requests

from src.crossref import resolve_doi
from src.config import CROSSREF_BASE_URL

TEST_EMAIL = "test@example.com"


@responses.activate
def test_resolve_doi_success():
    doi = "10.1037/example.0001"
    responses.add(
        responses.GET,
        f"{CROSSREF_BASE_URL}/{doi}",
        json={
            "message": {
                "title": ["A Study on Something Important"],
                "author": [{"given": "Jane", "family": "Smith"}],
                "container-title": ["Journal of Examples"],
                "published-print": {"date-parts": [[2020, 5, 1]]},
            }
        },
        status=200,
    )

    result = resolve_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "resolved"
    assert result.title == "A Study on Something Important"
    assert result.authors == ["Jane Smith"]
    assert result.container_title == "Journal of Examples"
    assert result.published_date == "2020-5-1"


@responses.activate
def test_resolve_doi_not_found_is_not_retried():
    doi = "10.9999/does-not-exist"
    responses.add(
        responses.GET,
        f"{CROSSREF_BASE_URL}/{doi}",
        json={"status": "not-found"},
        status=404,
    )

    result = resolve_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "not_found"
    # exactly one call -- 404 must NOT trigger retries
    assert len(responses.calls) == 1


@responses.activate
def test_resolve_doi_retries_on_500_then_succeeds():
    doi = "10.1037/example.0001"
    responses.add(responses.GET, f"{CROSSREF_BASE_URL}/{doi}", status=500)
    responses.add(responses.GET, f"{CROSSREF_BASE_URL}/{doi}", status=500)
    responses.add(
        responses.GET,
        f"{CROSSREF_BASE_URL}/{doi}",
        json={"message": {"title": ["Recovered After Retries"]}},
        status=200,
    )

    result = resolve_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "resolved"
    assert result.title == "Recovered After Retries"
    assert len(responses.calls) == 3


@responses.activate
def test_resolve_doi_exhausts_retries_and_reports_error():
    doi = "10.1037/example.0001"
    # MAX_RETRIES is 3 -- always fail
    for _ in range(3):
        responses.add(responses.GET, f"{CROSSREF_BASE_URL}/{doi}", status=503)

    result = resolve_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "error"
    assert result.error_message is not None
    assert len(responses.calls) == 3


@responses.activate
def test_resolve_doi_malformed_json_reports_error():
    doi = "10.1037/example.0001"
    responses.add(
        responses.GET,
        f"{CROSSREF_BASE_URL}/{doi}",
        body="not json",
        status=200,
        content_type="application/json",
    )

    result = resolve_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "error"
    assert "Malformed response" in result.error_message
