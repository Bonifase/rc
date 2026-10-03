import responses

from src.pmc_id_converter import convert_doi
from src.config import NCBI_ID_CONVERTER_URL, NCBI_EUTILS_BASE_URL

TEST_EMAIL = "test@example.com"


@responses.activate
def test_convert_doi_success_with_pmid_and_pmcid():
    doi = "10.1037/example.0001"
    responses.add(
        responses.GET,
        NCBI_ID_CONVERTER_URL,
        json={
            "status": "ok",
            "records": [{"doi": doi, "pmid": "12345678", "pmcid": "PMC9876543"}],
        },
        status=200,
    )

    result = convert_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "converted"
    assert result.pmid == "12345678"
    assert result.pmcid == "PMC9876543"


@responses.activate
def test_convert_doi_success_with_pmid_only_no_pmcid():
    # A valid, indexed DOI that just isn't in PMC (e.g. non-open-access journal).
    doi = "10.1037/example.0002"
    responses.add(
        responses.GET,
        NCBI_ID_CONVERTER_URL,
        json={"status": "ok", "records": [{"doi": doi, "pmid": "11112222"}]},
        status=200,
    )

    result = convert_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "converted"
    assert result.pmid == "11112222"
    assert result.pmcid is None


@responses.activate
def test_convert_doi_pmid_only_is_upgraded_to_pmcid_via_pmid_lookup():
    # The DOI lookup yields only a PMID, but the paper IS in PMC. The
    # PMID -> PMCID upgrade must recover the PMCID so full text is fetchable.
    doi = "10.1037/example.0003"
    responses.add(
        responses.GET,
        NCBI_ID_CONVERTER_URL,
        json={"status": "ok", "records": [{"doi": doi, "pmid": "11112222"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        NCBI_ID_CONVERTER_URL,
        json={"status": "ok", "records": [{"pmid": "11112222", "pmcid": "PMC44445555"}]},
        status=200,
    )

    result = convert_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "converted"
    assert result.pmid == "11112222"
    assert result.pmcid == "PMC44445555"
    assert len(responses.calls) == 2
    # The second call must have been the PMID -> PMCID lookup (idtype=pmid).
    assert "idtype=pmid" in responses.calls[1].request.url


@responses.activate
def test_esearch_fallback_pmid_is_then_upgraded_to_pmcid():
    # PMC ID Converter finds nothing; ESearch recovers a PMID; the PMID is
    # then upgraded to a PMCID by the PMID lookup.
    doi = "10.1037/example.0004"
    responses.add(
        responses.GET,
        NCBI_ID_CONVERTER_URL,
        json={"status": "ok", "records": []},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{NCBI_EUTILS_BASE_URL}/esearch.fcgi",
        json={"esearchresult": {"idlist": ["55556666"]}},
        status=200,
    )
    responses.add(
        responses.GET,
        NCBI_ID_CONVERTER_URL,
        json={"status": "ok", "records": [{"pmid": "55556666", "pmcid": "PMC77778888"}]},
        status=200,
    )

    result = convert_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "converted"
    assert result.pmid == "55556666"
    assert result.pmcid == "PMC77778888"
    assert len(responses.calls) == 3


@responses.activate
def test_pmid_lookup_failure_keeps_pmid_and_reports_converted():
    # PMID->PMCID is a best-effort upgrade: if it fails, we keep the PMID
    # and still report converted (a PubMed abstract beats nothing).
    doi = "10.1037/example.0005"
    responses.add(
        responses.GET,
        NCBI_ID_CONVERTER_URL,
        json={"status": "ok", "records": [{"doi": doi, "pmid": "11112222"}]},
        status=200,
    )
    for _ in range(3):
        responses.add(responses.GET, NCBI_ID_CONVERTER_URL, status=503)

    result = convert_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "converted"
    assert result.pmid == "11112222"
    assert result.pmcid is None


@responses.activate
def test_convert_doi_empty_records_is_not_indexed():
    # Valid DOI (per Crossref), but NCBI has nothing on file -- must be
    # distinguishable from "DOI doesn't exist" at the Crossref stage.
    doi = "10.5555/not-in-pubmed"
    responses.add(
        responses.GET,
        NCBI_ID_CONVERTER_URL,
        json={"status": "ok", "records": []},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{NCBI_EUTILS_BASE_URL}/esearch.fcgi",
        json={"esearchresult": {"idlist": []}},
        status=200,
    )

    result = convert_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "not_indexed"
    assert result.pmid is None
    assert result.pmcid is None


@responses.activate
def test_convert_doi_record_level_error_is_not_indexed():
    # NCBI's API reports per-record errors inside a 200 response, not via HTTP status.
    doi = "10.5555/not-in-pubmed"
    responses.add(
        responses.GET,
        NCBI_ID_CONVERTER_URL,
        json={"status": "ok", "records": [{"doi": doi, "status": "error", "errmsg": "unknown ID"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{NCBI_EUTILS_BASE_URL}/esearch.fcgi",
        json={"esearchresult": {"idlist": []}},
        status=200,
    )

    result = convert_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "not_indexed"


@responses.activate
def test_convert_doi_retries_on_500_then_succeeds():
    doi = "10.1037/example.0001"
    responses.add(responses.GET, NCBI_ID_CONVERTER_URL, status=500)
    responses.add(responses.GET, NCBI_ID_CONVERTER_URL, status=500)
    responses.add(
        responses.GET,
        NCBI_ID_CONVERTER_URL,
        json={"status": "ok", "records": [{"doi": doi, "pmid": "99999999"}]},
        status=200,
    )
    # The recovered PMID triggers the PMID -> PMCID upgrade.
    responses.add(
        responses.GET,
        NCBI_ID_CONVERTER_URL,
        json={"status": "ok", "records": [{"pmid": "99999999", "pmcid": "PMC1234567"}]},
        status=200,
    )

    result = convert_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "converted"
    assert result.pmcid == "PMC1234567"
    assert len(responses.calls) == 4


@responses.activate
def test_convert_doi_exhausts_retries_and_reports_error():
    doi = "10.1037/example.0001"
    for _ in range(3):
        responses.add(responses.GET, NCBI_ID_CONVERTER_URL, status=503)

    result = convert_doi(doi, contact_email=TEST_EMAIL)

    assert result.status == "error"
    assert len(responses.calls) == 3
