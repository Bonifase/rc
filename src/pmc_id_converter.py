"""
Step 2: Convert a DOI to PMID/PMCID via NCBI's ID Converter API.

Distinct outcomes (must NOT be collapsed):
- converted:      got at least a PMID and/or PMCID back
- not_indexed:    NCBI recognizes the request but has no record for this DOI --
                   this is a REAL, different outcome from Crossref's "not_found".
                   A DOI can be a perfectly valid reference and still land here
                   simply because the journal isn't PubMed-indexed.
- error:          transient failure (network, 5xx, malformed response) -- retried

We use the ID Converter API (https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/)
because it's a single call that accepts a DOI directly and returns PMID/PMCID
in one shot, rather than requiring a separate ESearch + parse step.
"""
from dataclasses import dataclass
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import (
    NCBI_ID_CONVERTER_URL,
    NCBI_EUTILS_BASE_URL,
    REQUEST_TIMEOUT_SECONDS,
    MAX_RETRIES,
    require_contact_email,
    NCBI_API_KEY,
)


class NcbiTransientError(Exception):
    """Raised for retryable failures (timeouts, 5xx, connection errors)."""


@dataclass
class PmcIdResult:
    doi: str
    status: str  # "converted" | "not_indexed" | "error"
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    error_message: Optional[str] = None


@retry(
    reraise=True,
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(NcbiTransientError),
)
def _fetch(doi: str, contact_email: str) -> requests.Response:
    params = {
        "ids": doi,
        "idtype": "doi",
        "format": "json",
        "tool": "citation-fidelity-checker",
        "email": contact_email,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY

    try:
        response = requests.get(
            NCBI_ID_CONVERTER_URL,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        # Network-level failures (timeouts, connection resets, etc.) are just
        # as transient as a 5xx and must go through the same retry path --
        # raising the bare requests exception here would skip retry entirely,
        # since retry_if_exception_type only matches NcbiTransientError.
        raise NcbiTransientError(f"Network error calling NCBI ID Converter: {exc}") from exc
    if response.status_code >= 500 or response.status_code == 429:
        raise NcbiTransientError(f"NCBI ID Converter returned {response.status_code}")
    response.raise_for_status()
    return response


@retry(
    reraise=True,
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(NcbiTransientError),
)
def _lookup_pmcid(pmid: str, contact_email: str) -> Optional[str]:
    """
    Resolve a PMID we already hold to a PMCID via the ID Converter (idtype=pmid).

    This closes the PMID -> PMCID gap: when the DOI lookup only yields a PMID
    (or falls back to ESearch), a paper that IS in PubMed Central still has
    full text we can fetch, but only if we upgrade the PMID to a PMCID first.
    """
    params = {
        "ids": pmid,
        "idtype": "pmid",
        "format": "json",
        "tool": "citation-fidelity-checker",
        "email": contact_email,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY

    try:
        response = requests.get(
            NCBI_ID_CONVERTER_URL,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise NcbiTransientError(f"Network error calling NCBI ID Converter: {exc}") from exc
    if response.status_code >= 500 or response.status_code == 429:
        raise NcbiTransientError(f"NCBI ID Converter returned {response.status_code}")
    response.raise_for_status()

    try:
        payload = response.json()
        records = payload.get("records", [])
        if records and records[0].get("status") != "error":
            return records[0].get("pmcid")
    except ValueError:
        return None
    return None


@retry(
    reraise=True,
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(NcbiTransientError),
)
def _esearch_pmid(doi: str, contact_email: str) -> Optional[str]:
    """
    Fallback when the PMC ID Converter finds nothing: query PubMed ESearch by
    DOI to recover the PMID. Many papers are PubMed-indexed (have a PMID and
    an abstract) but are NOT in PubMed Central (no PMCID), which the ID
    Converter reports as "Identifier not found in PMC" even though a real
    PMID exists. Returns the PMID, or None if PubMed also has no record.
    """
    params = {
        "db": "pubmed",
        "term": f"{doi}[DOI]",
        "retmode": "json",
        "tool": "citation-fidelity-checker",
        "email": contact_email,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY

    try:
        response = requests.get(
            f"{NCBI_EUTILS_BASE_URL}/esearch.fcgi",
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise NcbiTransientError(f"Network error calling NCBI ESearch: {exc}") from exc
    if response.status_code >= 500 or response.status_code == 429:
        raise NcbiTransientError(f"NCBI ESearch returned {response.status_code}")
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError:
        return None

    idlist = payload.get("esearchresult", {}).get("idlist", [])
    return idlist[0] if idlist else None


def convert_doi(doi: str, contact_email: Optional[str] = None) -> PmcIdResult:
    """
    Convert a single DOI to PMID/PMCID. Treats "record not found" as a final,
    non-retryable outcome distinct from a transient error -- an unindexed DOI
    is information the pipeline needs to preserve, not a failure to recover from.

    The PMC ID Converter only reports PMCID matches; papers that are
    PubMed-indexed but NOT in PubMed Central come back as "Identifier not
    found in PMC" with no PMID. Those are still findable in PubMed via ESearch,
    so on any "no record" from the ID Converter we fall back to a PMID lookup
    before concluding the source is unindexed.
    """
    email = contact_email or require_contact_email()
    doi = doi.strip()

    try:
        response = _fetch(doi, email)
    except (NcbiTransientError, requests.RequestException) as exc:
        return PmcIdResult(doi=doi, status="error", error_message=str(exc))

    pmid = None
    pmcid = None

    try:
        payload = response.json()
        records = payload.get("records", [])
        if records:
            record = records[0]
            if record.get("status") != "error":
                pmid = record.get("pmid")
                pmcid = record.get("pmcid")
    except ValueError as exc:
        return PmcIdResult(doi=doi, status="error", error_message=f"Malformed response: {exc}")

    # ID Converter found nothing (or only an error status) -> try PubMed.
    if not pmid and not pmcid:
        try:
            pmid = _esearch_pmid(doi, email)
        except (NcbiTransientError, requests.RequestException) as exc:
            return PmcIdResult(doi=doi, status="error", error_message=str(exc))

    # A PMID we recovered may still have a PMCID behind it: upgrading it lets
    # the content fetcher read full text from PMC instead of settling for the
    # abstract. If the PMID->PMCID lookup fails, keep the PMID -- a PubMed
    # abstract is still better than nothing, and this is a best-effort upgrade.
    if pmid and not pmcid:
        try:
            pmcid = _lookup_pmcid(pmid, email)
        except (NcbiTransientError, requests.RequestException):
            pmcid = None

    if not pmid and not pmcid:
        return PmcIdResult(doi=doi, status="not_indexed")

    return PmcIdResult(doi=doi, status="converted", pmid=pmid, pmcid=pmcid)
