"""
Fetches the richer Crossref record the metadata module needs: authors as
structured family/given (not just joined display names), volume, issue,
page, ISSN, and publication type -- fields src/crossref.py (built for the
content pipeline) doesn't carry because the content pipeline never needed
them.

Deliberately does not import from src/crossref.py or share its HTTP
session/retry decorator. The metadata module is meant to run fully
independently of the content pipeline (per the spec: "no dependency on v16
or the content pipeline") -- sharing code across that boundary would
recreate the coupling the spec is explicitly designing around, even though
some of this logic (retry policy, 404-vs-5xx handling) rhymes with
src/crossref.py by necessity, since it's the same external API.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import CROSSREF_BASE_URL, REQUEST_TIMEOUT_SECONDS, MAX_RETRIES, require_contact_email
from src.metadata.extraction import Author

_WORKS_SEARCH_URL = CROSSREF_BASE_URL  # e.g. https://api.crossref.org/works


class CrossrefMetadataTransientError(Exception):
    """Raised for retryable failures (timeouts, 5xx, connection errors)."""


@dataclass
class ActualReference:
    """What Crossref actually says about a paper -- the record a
    DeclaredReference (src/metadata/extraction.py) gets compared against."""
    doi: str
    status: str  # "resolved" | "not_found" | "error"
    title: Optional[str] = None
    authors: list[Author] = field(default_factory=list)
    year: Optional[int] = None
    journal: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    issn: Optional[str] = None
    publication_type: Optional[str] = None  # Crossref "type", e.g. "journal-article" -- extraction only, not a check
    retracted: bool = False  # record carries a retraction update (check 15)
    retraction_notice: Optional[str] = None  # DOI of the retraction notice
    retraction_source: Optional[str] = None  # "retraction-watch" | "publisher"
    title_match_score: Optional[float] = None  # only set by search_by_title_author -- similarity of the best candidate found
    error_message: Optional[str] = None


def _extract_authors(message: dict) -> list[Author]:
    authors = []
    for a in message.get("author", []) or []:
        family = a.get("family")
        given = a.get("given")
        if family or given:
            authors.append(Author(family=family, given=given))
    return authors


def _extract_year(message: dict) -> Optional[int]:
    for key in ("published-print", "published-online", "published"):
        date_parts = message.get(key, {}).get("date-parts")
        if date_parts and date_parts[0] and date_parts[0][0]:
            try:
                return int(date_parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _extract_issn(message: dict) -> Optional[str]:
    issns = message.get("ISSN") or []
    return issns[0] if issns else None


def _extract_retraction(message: dict) -> tuple[bool, Optional[str], Optional[str]]:
    """Detects whether a record is retracted, per Crossref's Retraction Watch
    integration (free since the 2023 Crossref acquisition of the RW database;
    no second provider needed).

    Two placements cover both publisher conventions:
      - `updated-by` on the PAPER itself: an entry type "retraction" means this
        paper is the subject of a retraction notice (the marker Retraction Watch
        injects into the original DOI's record).
      - `update-to` on a NOTICE: an entry type "retraction" means this record IS
        the retraction notice (Hindawi-style "[Retracted]" re-publications carry
        this instead of updated-by).

    The `source` distinguishes retraction-watch records from publisher-registered
    ones; both count as retracted.
    """
    for key in ("updated-by", "update-to"):
        for entry in message.get(key) or []:
            if (entry.get("type") or "").lower() == "retraction":
                return True, entry.get("DOI") or None, entry.get("source") or None
    return False, None, None


def _parse_message(doi: str, message: dict) -> ActualReference:
    retracted, retraction_notice, retraction_source = _extract_retraction(message)
    return ActualReference(
        doi=doi,
        status="resolved",
        title=(message.get("title") or [None])[0],
        authors=_extract_authors(message),
        year=_extract_year(message),
        journal=(message.get("container-title") or [None])[0],
        volume=message.get("volume"),
        issue=message.get("issue"),
        pages=message.get("page"),
        issn=_extract_issn(message),
        publication_type=message.get("type"),
        retracted=retracted,
        retraction_notice=retraction_notice,
        retraction_source=retraction_source,
    )


@retry(
    reraise=True,
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(CrossrefMetadataTransientError),
)
def _get(url: str, params: dict) -> requests.Response:
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    if response.status_code == 404:
        return response  # final, non-retryable outcome -- not a transient failure
    if response.status_code >= 500 or response.status_code == 429:
        raise CrossrefMetadataTransientError(f"Crossref returned {response.status_code}")
    response.raise_for_status()
    return response


def fetch_by_doi(doi: str, contact_email: Optional[str] = None) -> ActualReference:
    """Fetches the full record for a known DOI."""
    email = contact_email or require_contact_email()
    doi = doi.strip()

    try:
        response = _get(f"{_WORKS_SEARCH_URL}/{doi}", {"mailto": email})
    except (CrossrefMetadataTransientError, requests.RequestException) as exc:
        return ActualReference(doi=doi, status="error", error_message=str(exc))

    if response.status_code == 404:
        return ActualReference(doi=doi, status="not_found")

    try:
        message = response.json()["message"]
    except (ValueError, KeyError) as exc:
        return ActualReference(doi=doi, status="error", error_message=f"Malformed response: {exc}")

    return _parse_message(doi, message)


def fetch_journal_volume_samples(issn: str, contact_email: Optional[str] = None, rows: int = 20) -> list[tuple[int, str]]:
    """
    Samples up to `rows` records from a journal (by ISSN), returning
    (year, volume) pairs sorted by year -- the data check_11 uses to
    approximate that journal's published volume range, per the spec's
    fallback: "approximate from the year<->volume relationship across
    several records from the same journal" (used because Crossref has no
    direct "volume range for this ISSN" endpoint).

    This is a genuinely separate API call from fetch_by_doi/
    search_by_title_author -- callers citing the same journal repeatedly
    should cache the result per ISSN rather than calling this once per
    reference (see phase_b.py's check_11, which does exactly that).
    """
    email = contact_email or require_contact_email()
    try:
        response = _get(
            f"https://api.crossref.org/journals/{issn}/works",
            {"rows": rows, "sort": "published", "order": "asc", "mailto": email},
        )
    except (CrossrefMetadataTransientError, requests.RequestException):
        return []

    if response.status_code == 404:
        return []

    try:
        items = response.json()["message"]["items"]
    except (ValueError, KeyError):
        return []

    samples = []
    for item in items:
        year = _extract_year(item)
        volume = item.get("volume")
        if year is not None and volume:
            samples.append((year, volume))
    return sorted(samples, key=lambda pair: pair[0])


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation -- for fuzzy comparison, not display."""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def title_similarity(a: str, b: str) -> float:
    """0.0-1.0 fuzzy similarity between two titles, punctuation/case-insensitive."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, _normalize_title(a), _normalize_title(b)).ratio()


def search_by_title_author(
    title: str,
    first_author_family: Optional[str],
    contact_email: Optional[str] = None,
) -> ActualReference:
    """
    Used both as the primary resolution route when a reference has no DOI
    (or its DOI doesn't resolve), and as the "rescue" step when a DOI
    resolves to the wrong paper. Queries Crossref's bibliographic search and
    returns the best-scoring candidate (by title similarity, see
    title_similarity()) with that score attached as title_match_score.

    Deliberately does NOT apply the 0.85 similarity threshold itself and
    collapse a weak match into not_found -- per the spec's resolution flow,
    "found, but title similarity is low" and "found and matches well" are
    two different, both-non-blocking outcomes (synthesized_title vs.
    proceed), while "not found at all" is a third, blocking one. Only the
    caller (src/metadata/resolution.py) has enough context -- which branch
    of the flow it's being called from -- to apply that threshold
    correctly, so this function's job stops at "here's the best candidate
    and how confident the match is," not "pass or fail."

    status="not_found" only when Crossref's search returns zero results, or
    none of them have a title to score against.
    """
    email = contact_email or require_contact_email()
    if not title or not title.strip():
        return ActualReference(doi="", status="not_found", error_message="No title available to search on.")

    query = title if not first_author_family else f"{title} {first_author_family}"

    try:
        response = _get(_WORKS_SEARCH_URL, {"query.bibliographic": query, "rows": 5, "mailto": email})
    except (CrossrefMetadataTransientError, requests.RequestException) as exc:
        return ActualReference(doi="", status="error", error_message=str(exc))

    try:
        items = response.json()["message"]["items"]
    except (ValueError, KeyError) as exc:
        return ActualReference(doi="", status="error", error_message=f"Malformed response: {exc}")

    best_item = None
    best_score = -1.0
    for item in items:
        candidate_title = (item.get("title") or [None])[0]
        if not candidate_title:
            continue
        score = title_similarity(title, candidate_title)
        if score > best_score:
            best_score = score
            best_item = item

    if best_item is None:
        return ActualReference(doi="", status="not_found")

    doi = best_item.get("DOI", "")
    result = _parse_message(doi, best_item)
    result.title_match_score = best_score
    return result
