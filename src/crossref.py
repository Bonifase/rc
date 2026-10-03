"""
Step 1: Resolve a DOI via Crossref.

Distinct outcomes (per the guide, these must NOT be collapsed together):
- resolved:     DOI exists, metadata returned
- not_found:    DOI doesn't resolve (404) -- a real outcome, not a retry-able error
- error:        something else went wrong (network, 5xx, malformed response) --
                this DOES get retried
"""
from dataclasses import dataclass, field
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import CROSSREF_BASE_URL, REQUEST_TIMEOUT_SECONDS, MAX_RETRIES, require_contact_email


class CrossrefTransientError(Exception):
    """Raised for retryable failures (timeouts, 5xx, connection errors)."""


@dataclass
class CrossrefResult:
    doi: str
    status: str  # "resolved" | "not_found" | "error"
    title: Optional[str] = None
    authors: Optional[list] = field(default=None)
    container_title: Optional[str] = None
    published_date: Optional[str] = None
    abstract: Optional[str] = None
    error_message: Optional[str] = None


def _extract_authors(message: dict) -> list:
    authors = []
    for a in message.get("author", []) or []:
        given = a.get("given", "")
        family = a.get("family", "")
        name = f"{given} {family}".strip()
        if name:
            authors.append(name)
    return authors


def _extract_published_date(message: dict) -> Optional[str]:
    for key in ("published-print", "published-online", "published"):
        date_parts = message.get(key, {}).get("date-parts")
        if date_parts and date_parts[0]:
            return "-".join(str(p) for p in date_parts[0])
    return None


def _parse_crossref_message(doi: str, message: dict) -> CrossrefResult:
    return CrossrefResult(
        doi=doi,
        status="resolved",
        title=(message.get("title") or [None])[0],
        authors=_extract_authors(message),
        container_title=(message.get("container-title") or [None])[0],
        published_date=_extract_published_date(message),
        # Crossref abstracts, when present, come back as JATS-ish XML/HTML.
        # Leave raw here; content_fetcher.py / callers can strip tags if needed.
        abstract=message.get("abstract"),
    )


@retry(
    reraise=True,
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(CrossrefTransientError),
)
def _fetch(doi: str, contact_email: str) -> requests.Response:
    try:
        response = requests.get(
            f"{CROSSREF_BASE_URL}/{doi}",
            params={"mailto": contact_email},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        # Network-level failures must go through the same retry path as a
        # 5xx -- raising the bare requests exception here bypasses tenacity,
        # since retry_if_exception_type only matches CrossrefTransientError.
        raise CrossrefTransientError(f"Network error calling Crossref: {exc}") from exc
    if response.status_code == 404:
        # Not a transient failure -- don't retry, let caller handle it as its own outcome.
        return response
    if response.status_code >= 500 or response.status_code == 429:
        raise CrossrefTransientError(f"Crossref returned {response.status_code}")
    response.raise_for_status()
    return response


def resolve_doi(doi: str, contact_email: Optional[str] = None) -> CrossrefResult:
    """
    Resolve a single DOI via Crossref. Retries on transient errors (timeouts,
    5xx, 429) with exponential backoff; treats 404 as a final, non-retryable
    outcome since a missing DOI is information, not a failure.
    """
    email = contact_email or require_contact_email()
    doi = doi.strip()

    try:
        response = _fetch(doi, email)
    except CrossrefTransientError as exc:
        return CrossrefResult(doi=doi, status="error", error_message=str(exc))
    except requests.RequestException as exc:
        return CrossrefResult(doi=doi, status="error", error_message=str(exc))

    if response.status_code == 404:
        return CrossrefResult(doi=doi, status="not_found")

    try:
        payload = response.json()
        message = payload["message"]
    except (ValueError, KeyError) as exc:
        return CrossrefResult(doi=doi, status="error", error_message=f"Malformed response: {exc}")

    return _parse_crossref_message(doi, message)
