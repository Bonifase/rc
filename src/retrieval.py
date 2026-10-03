"""
Extended retrieval: resolve and fetch source content beyond plain PMC/PubMed.

Purpose (Eduardo's directive, "extended retrival" thread):
  - Search by title + first author + year when no identifier exists.
  - Fallbacks through open-access sources: Europe PMC, Unpaywall, OpenAlex,
    Semantic Scholar, open-access publishers (PLOS, Frontiers, MDPI, ...),
    institutional repositories (DSpace 7 handle resolution), open publisher
    pages (HTML/PDF).
  - Identity check on every retrieved source: exact DOI when available,
    otherwise normalized title + first author + publication year.
    Ambiguous or conflicting matches produce a retrieval abstention, never
    an automatic match.
  - Preserve the final URL and the content hash (sha256 of the raw bytes).
  - Structured fields in every case: abstract_text, body_text,
    retrieval_source, retrieval_status, structure_status
    (resolved / unresolved / not_applicable). Where abstract and body cannot
    be split, record "unresolved" rather than merging silently.

The module never calls LLMs and never writes to the repo; it only reads
remote sources. Errors are returned as structured results, not raised.
"""
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import CONTACT_EMAIL

_EUROPE_PMC_BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_UNPAYWALL_BASE_URL = "https://api.unpaywall.org/v2"
_OPENALEX_BASE_URL = "https://api.openalex.org/works"
_CROSSREF_BASE_URL = "https://api.crossref.org/works"


class RetrievalTransientError(Exception):
    """Raised for retryable failures (timeouts, 5xx, 429, connection errors)."""


@dataclass
class Citation:
    """Minimum identifying set for a cited source (from the reference list /
    the gold metadata). All fields optional; identity narrows as more are set."""
    title: Optional[str] = None
    first_author: Optional[str] = None
    publication_year: Optional[int] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    journal: Optional[str] = None


@dataclass
class Candidate:
    """A bibliographic record surfaced by one of the aggregators, before the
    content stage. Passes the caller call open_access info through."""
    source: str
    doi: Optional[str] = None
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    title: Optional[str] = None
    authors: Optional[list] = None
    year: Optional[int] = None
    abstract: Optional[str] = None
    open_access: Optional[dict] = None
    landing_url: Optional[str] = None
    pdf_url: Optional[str] = None
    is_oa: bool = False
    container: Optional[str] = None


@dataclass
class ExtendedRetrievalResult:
    status: str  # "full_text" | "abstract_only" | "not_found" | "abstained" | "error"
    abstract_text: Optional[str] = None
    body_text: Optional[str] = None
    retrieval_source: Optional[str] = None
    retrieval_status: Optional[str] = None  # "full" | "abstract_only" | "landing_only" | "not_found" | "abstained" | "error"
    structure_status: Optional[str] = None  # "resolved" | "unresolved" | "not_applicable"
    final_url: Optional[str] = None
    content_hash: Optional[str] = None
    matched_candidate: Optional[dict] = None
    identity_note: Optional[str] = None
    abstained_reason: Optional[str] = None
    error_message: Optional[str] = None
    candidates_seen: Optional[list] = field(default=None)

    @property
    def text(self) -> str:
        """Text a judge would read: body if any, else abstract."""
        return (self.body_text or "").strip() or (self.abstract_text or "").strip()


# ---------------------------------------------------------------------------
# HTTP plumbing (+ retries)
# ---------------------------------------------------------------------------

def _contact_email() -> str:
    return (CONTACT_EMAIL or "").strip() or "zablon@example.org"


_HEADERS = {"User-Agent": "citation-fidelity-checker (mailto:" + _contact_email() + ")"}


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(RetrievalTransientError),
)
def _get_bytes(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
               timeout: int = 60) -> tuple[bytes, str, str]:
    """GET a URL, return (raw_bytes, resolved_url). Retries network/5xx/429."""
    resp = requests.get(url, params=params, headers=headers or _HEADERS, timeout=timeout)
    if resp.status_code >= 500 or resp.status_code == 429:
        raise RetrievalTransientError(f"GET {url} -> {resp.status_code}")
    if resp.status_code == 404:
        return b"", resp.url, resp.url
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", ""), resp.url


def _get_json(url: str, params: Optional[dict] = None, **kw) -> Optional[dict]:
    raw, ctype, final = _get_bytes(url, params=params, **kw)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Normalization + identity checks
# ---------------------------------------------------------------------------

_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _ALNUM_RE.sub(" ", (s or "").lower()).strip()


def _norm_compact(s: str) -> str:
    return _ALNUM_RE.sub("", (s or "").lower())


def _title_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _first_author_family(author_str: Optional[str]) -> str:
    """Extract the FAMILY surname from an author string, not the given name.

    - 'Codina, M. Estefania' (Family, Given) -> 'codina'  (family first)
    - 'Marta Codina'          (Given Family) -> 'codina'  (family last)
    - 'Jane Smith'            (Given Family) -> 'smith'   (family last)
    - 'Wei'                   (single token) -> 'wei'
    """
    if not author_str:
        return ""
    s = author_str.strip()
    if not s:
        return ""
    if "," in s:
        # "Family, Given ..." -> the family is the leading token.
        family_part = s.split(",", 1)[0]
        tokens = [t for t in re.split(r"\s+", family_part) if t]
        return tokens[0].lower() if tokens else ""
    # "Given Family" (space separated) -> the family is the last token.
    tokens = [t for t in re.split(r"\s+", s) if t]
    return tokens[-1].lower() if tokens else ""


_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _author_name_hits(declared_first: str, author_names) -> bool:
    """True if the declared first author appears among the candidate's
    author strings, either as a whole author or as a family surname token.
    Handles 'Given Family', 'Family, Given' and multi-token names."""
    declared = _norm_compact(declared_first)
    declared_family = _first_author_family(declared_first)
    wanted = {s for s in (declared, declared_family) if s}
    for a in author_names or []:
        compact = _norm_compact(a)
        if compact == declared:
            return True
        tokens = _TOKEN_RE.findall(_norm(a))
        if any(t in wanted for t in tokens):
            return True
    return False


def candidate_identity(citation: Citation, cand: Candidate) -> tuple[bool, str]:
    """Eduardo's identity rule: exact DOI if available; otherwise normalized
    title + first author + publication year."""
    if citation.doi:
        if cand.doi and _norm_compact(cand.doi) == _norm_compact(citation.doi):
            return True, f"exact DOI {citation.doi}"
        return False, f"DOI mismatch: citation {citation.doi} vs candidate {cand.doi or '(none)'}"
    if not citation.title:
        return False, "no DOI and no title to identity-check against"
    title_ok = _title_ratio(citation.title, cand.title or "") >= 0.85
    author_ok = bool(citation.first_author) and bool(cand.authors) and _author_name_hits(
        citation.first_author, cand.authors
    )
    if not title_ok:
        return False, f"title mismatch (ratio {_title_ratio(citation.title, cand.title or ''):.2f})"
    if not author_ok:
        return False, "first-author mismatch"
    if citation.publication_year:
        if not cand.year:
            # The rule is title + first author + publication YEAR. A candidate
            # that cannot confirm the declared year does not satisfy it.
            return False, (f"candidate missing publication year "
                           f"(citation {citation.publication_year})")
        if cand.year != citation.publication_year:
            return False, (f"year conflict: citation {citation.publication_year} "
                           f"vs candidate {cand.year}")
    return True, "normalized title + first author + year"


def _content_identity(citation: Citation, text: str) -> tuple[bool, str]:
    """Identity of a fetched document against its retrieved CONTENT. Used for
    publisher pages / repository full text where aggregator metadata may be
    thin: the normalized title plus first-author surname and year must appear
    in the fetched text."""
    text_norm = _norm(text)
    text_compact = _norm_compact(text)
    checks = []
    ok = True
    if citation.title and len(_norm(citation.title)) >= 12:
        t = _norm_compact(citation.title)
        hit = t in text_compact
        checks.append(("title", hit))
        ok = ok and hit
    if citation.first_author:
        surname = _first_author_family(citation.first_author)
        hit = surname and surname in text_compact
        checks.append(("first_author", bool(hit)))
        ok = ok and bool(hit)
    if citation.publication_year:
        yr = str(citation.publication_year)
        hit = yr in text_compact
        checks.append(("year", hit))
        ok = ok and hit
    if not checks:
        return False, "nothing to identity-check against"
    detail = "; ".join(f"{k}={v}" for k, v in checks)
    return ok, ("content identity pass (" + detail + ")") if ok else ("content identity FAIL (" + detail + ")")


# ---------------------------------------------------------------------------
# Aggregator clients (search + lookup)
# ---------------------------------------------------------------------------

def _openalex_search(citation: Citation) -> list[Candidate]:
    params = {"per-page": "10", "mailto": _contact_email()}
    from src.config import OPENALEX_API_KEY
    if OPENALEX_API_KEY:
        params["api_key"] = OPENALEX_API_KEY
    # Deliberately NO publication_year filter here: identity (title + first
    # author + year, matched against content) is the gate, not discovery. A
    # year-conflicting near-miss must be seen so the identity check can abstain
    # on it instead of silently never finding it.
    filters = [f"title.search:{_norm(citation.title or '')}"]
    params["filter"] = ",".join(filters)
    params["select"] = "id,doi,title,publication_year,authorships,best_oa_location,locations,primary_location,language"
    data = _get_json(_OPENALEX_BASE_URL, params=params)
    if not data:
        return []
    out = []
    for w in data.get("results", []) or []:
        authors = [(a.get("author") or {}).get("display_name") for a in w.get("authorships", [])]
        loc = (w.get("best_oa_location") or {}) or {}
        if not loc and w.get("primary_location"):
            loc = w["primary_location"]
        out.append(Candidate(
            source="openalex",
            doi=(w.get("doi") or "").replace("https://doi.org/", "") or None,
            title=w.get("title") or w.get("display_name"),
            authors=authors,
            year=w.get("publication_year"),
            pdf_url=loc.get("pdf_url"),
            landing_url=loc.get("landing_page_url"),
            is_oa=bool(loc.get("is_oa")),
        ))
    return out


def _openalex_by_doi(doi: str) -> Optional[Candidate]:
    params = {"mailto": _contact_email()}
    from src.config import OPENALEX_API_KEY
    if OPENALEX_API_KEY:
        params["api_key"] = OPENALEX_API_KEY
    params["select"] = "id,doi,title,publication_year,authorships,best_oa_location,primary_location"
    data = _get_json(_OPENALEX_BASE_URL + "/" + uri_quote(doi), params=params)
    if not data or data.get("doi") is None:
        return None
    authors = [(a.get("author") or {}).get("display_name") for a in data.get("authorships", [])]
    loc = (data.get("best_oa_location") or {}) or {}
    return Candidate(
        source="openalex", doi=(data.get("doi") or "").replace("https://doi.org/", "") or None,
        title=data.get("title"), authors=authors, year=data.get("publication_year"),
        pdf_url=loc.get("pdf_url"), landing_url=loc.get("landing_page_url"),
        is_oa=bool(loc.get("is_oa")),
    )


def _crossref_search(citation: Citation) -> list[Candidate]:
    params = {
        "query.bibliographic": citation.title or "",
        "rows": "10",
        "select": "DOI,title,author,container-title,issued,abstract",
    }
    if citation.first_author:
        params["query.author"] = citation.first_author
    if citation.publication_year:
        y = citation.publication_year
        params["filter"] = f"from-pub-date:{y}-01-01,until-pub-date:{y}-12-31"
    data = _get_json(_CROSSREF_BASE_URL, params=params)
    if not data:
        return []
    out = []
    for it in (data.get("message") or {}).get("items", []) or []:
        author_names = [f"{a.get('given','')} {a.get('family','')}".strip()
                        for a in it.get("author", []) if a.get("family")]
        year = None
        for key in ("issued", "published", "published-print", "published-online"):
            parts = (it.get(key) or {}).get("date-parts") or []
            if parts and parts[0] and parts[0][0]:
                year = int(parts[0][0])
                break
        abstract = it.get("abstract")
        if abstract:
            abstract = re.sub(r"<[^>]+>", " ", abstract)
            abstract = " ".join(abstract.split())
        out.append(Candidate(
            source="crossref", doi=it.get("DOI"), title=(it.get("title") or [None])[0],
            authors=author_names, year=year, container=(it.get("container-title") or [None])[0],
            abstract=abstract or None,
        ))
    return out


def _crossref_by_doi(doi: str) -> Optional[Candidate]:
    data = _get_json(_CROSSREF_BASE_URL + "/" + uri_quote(doi))
    if not data:
        return None
    it = data.get("message") or {}
    author_names = [f"{a.get('given','')} {a.get('family','')}".strip()
                    for a in it.get("author", []) if a.get("family")]
    year = None
    for key in ("issued", "published-print", "published-online", "published"):
        parts = (it.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break
    abstract = it.get("abstract")
    if abstract:
        abstract = " ".join(re.sub(r"<[^>]+>", " ", abstract).split())
    return Candidate(
        source="crossref", doi=doi, title=(it.get("title") or [None])[0],
        authors=author_names, year=year, container=(it.get("container-title") or [None])[0],
        abstract=abstract or None,
    )


def _europepmc_search(citation: Citation) -> list[Candidate]:
    if not citation.title:
        return []
    q = []
    if citation.title:
        q.append(f'TITLE:"{citation.title}"')
    if citation.first_author:
        q.append(f'AUTH:"{citation.first_author}"')
    if citation.publication_year:
        q.append(f"PUB_YEAR:{citation.publication_year}")
    params = {"query": " AND ".join(q), "format": "json", "pageSize": "10", "resultType": "lite"}
    data = _get_json(_EUROPE_PMC_BASE_URL + "/search", params=params)
    if not data:
        return []
    out = []
    for it in (data.get("resultList") or {}).get("result", []) or []:
        pmcids = [p for p in (it.get("fullTextIdList") or {}).get("fullTextId", []) or []]
        out.append(Candidate(
            source="europepmc", doi=it.get("doi"), pmid=it.get("pmid"),
            pmcid=pmcids[0] if pmcids else None,
            title=it.get("title"),
            authors=[s.strip() for s in (it.get("authorString") or "").split(",") if s.strip()],
            year=int(it["pubYear"]) if it.get("pubYear", "").isdigit() else None,
            container=it.get("journalTitle"), is_oa=it.get("isOpenAccess") == "Y",
        ))
    return out


def _semanticscholar_by_doi(doi: str) -> Optional[Candidate]:
    data = _get_json("https://api.semanticscholar.org/graph/v1/paper/DOI:" + uri_quote(doi),
                     params={"fields": "title,abstract,year,externalIds,authors,openAccessPdf,venue"}, timeout=45)
    if not data or data.get("title") is None:
        return None
    oa = data.get("openAccessPdf") or {}
    return Candidate(
        source="semantic_scholar", doi=doi, title=data.get("title"),
        authors=[a.get("name") for a in data.get("authors", []) if a.get("name")],
        year=data.get("year"), abstract=data.get("abstract"),
        container=data.get("venue"), is_oa=data.get("openAccessPdf"),
        pdf_url=(oa.get("url") or "").strip() or None,
    )


def _semanticscholar_search(citation: Citation) -> list[Candidate]:
    if not citation.title:
        return []
    data = _get_json("https://api.semanticscholar.org/graph/v1/paper/search",
                     params={"query": citation.title, "limit": "10", "fields":
                             "title,year,abstract,externalIds,authors,openAccessPdf"},
                     timeout=45)
    if not data:
        return []
    out = []
    # "total" is high because it's a fuzzy search over the whole index; only
    # candidates that pass identity downstream survive, so we pre-filter only
    # the top titles that look like the citation.
    for it in data.get("data", []) or []:
        oa = it.get("openAccessPdf") or {}
        out.append(Candidate(
            source="semantic_scholar", doi=(it.get("externalIds") or {}).get("DOI"),
            title=it.get("title"), authors=[a.get("name") for a in it.get("authors", []) if a.get("name")],
            year=it.get("year"), abstract=it.get("abstract"),
            pdf_url=(oa.get("url") or "").strip() or None,
        ))
    return out


def _unpaywall_locations(doi: str) -> list[dict]:
    data = _get_json(_UNPAYWALL_BASE_URL + "/" + uri_quote(doi),
                     params={"email": _contact_email()}, timeout=45)
    if not data:
        return []
    oas = data.get("oa_locations") or []
    best = data.get("best_oa_location")
    if best and best not in oas:
        oas = [best] + oas
    return [o for o in oas if isinstance(o, dict)]


def uri_quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="/:")


# ---------------------------------------------------------------------------
# Content parsing (JATS / HTML / PDF / DSpace repository)
# ---------------------------------------------------------------------------

def _jats_abstract_and_body(xml_bytes: bytes) -> tuple[Optional[str], Optional[str]]:
    """Split a JATS article into (abstract_text, body_text). Namespace-free
    (NCBI/Europe PMC output). Body excludes the front abstract/title/keywords
    so the structured fields are honest."""
    from xml.etree import ElementTree as ET
    from src.content_fetcher import _walk_abstract, _walk_body
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None, None

    abstract_blocks: list[str] = []
    meta = root.find(".//article-meta")
    if meta is None:
        meta = root.find(".//front/article-meta")
    if meta is not None:
        for ab in meta.findall("abstract"):
            _walk_abstract(ab, abstract_blocks)
    abstract_text = "\n\n".join(abstract_blocks).strip() or None

    body_blocks: list[str] = []
    for body in root.findall(".//body"):
        _walk_body(body, body_blocks)
    for floats_el in root.findall(".//floats-group") + root.findall(".//floats-wrap"):
        _walk_body(floats_el, body_blocks)
    for back_el in root.findall(".//back"):
        _walk_body(back_el, body_blocks)
    body_text = "\n\n".join(body_blocks).strip() or None
    return abstract_text, body_text


_ABSTRACT_HEADER_RE = re.compile(
    r"(^\s*abstract\s*$|^\s*abstract\s*[:\-]|^summary\s*:?$|^simple summary\s*:?$)"
)


_SECTION_START_RE = re.compile(
    r"^(introduction|background|methods?|materials?|methodology|results|"
    r"discussion|conclusions?|references|acknowledg|funding|supplement[ay]|"
    r"data\s+availability|contribut|declarations?|conflict|dedication|"
    r"keywords|\d+[\.\)]\s*[a-z]|[ivxlcdm]+[\.\)]\s*[a-z])",
    re.I,
)


_METADATA_LINE_RE = re.compile(
    r"^(doi|url|e?mail|correspond|affiliat|received|accepted|published|"
    r"copyright|citation|@|\d{4}|vol(ume)?\.?\s|issue|pages?\b|"
    r"j\s*[ivx]+\.?\s*\d)",
    re.I,
)


def _split_abstract_body_loose(text: str) -> tuple[Optional[str], Optional[str], str]:
    """Best-effort abstract/body split for HTML/PDF/repository text. If a
    clearly labeled abstract section exists, split on it so the body starts
    AFTER the abstract (no overlap with it); otherwise signal 'unresolved' --
    never fabricate a boundary."""
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    abstract_idx = None
    for i, ln in enumerate(lines):
        if _ABSTRACT_HEADER_RE.match(ln.lower()):
            abstract_idx = i
            break
    if abstract_idx is None:
        return None, text.strip(), "unresolved"

    # Where the abstract section ends and the body starts: the first real
    # section header (numbered or plain) that follows the abstract text.
    body_start = len(lines)
    k = abstract_idx + 1
    while k < len(lines):
        ln = lines[k]
        if not ln.strip() or _METADATA_LINE_RE.match(ln):
            k += 1
        else:
            break
    for idx in range(k, len(lines)):
        ln = lines[idx]
        if _SECTION_START_RE.match(ln.lower()):
            body_start = idx
            break
        body_start = idx + 1

    abs_lines = [ln.strip() for ln in lines[abstract_idx + 1:body_start]
                 if ln.strip()]
    abstract = " ".join(" ".join(abs_lines).split()).strip()
    # Body starts exactly where the abstract section ends, so the abstract
    # (heading + text) is never included in the body.
    body = "\n".join(lines[body_start:]).strip()
    if not abstract or len(abstract) < 40:
        return None, text.strip(), "unresolved"
    return abstract, body, "resolved"


class _HtmlTextParser(HTMLParser):
    """Dumb-but-safe HTML -> text: skip script/style/head, one line per block.
    Good enough for publisher landing pages; no external dependency."""

    _SKIP = {"script", "style", "noscript", "head", "title", "svg", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buf: list[str] = []
        self._skip_depth = 0
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag in ("p", "div", "section", "br", "li", "h1", "h2", "h3", "h4", "tr"):
            self._flush()

    def handle_startendtag(self, tag, attrs):
        if tag in ("br", "hr"):
            self._flush()

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag in ("p", "div", "section", "li", "tr"):
            self._flush()

    def handle_data(self, data):
        if not self._skip_depth:
            self._buf.append(data)

    def _flush(self):
        line = " ".join(" ".join(self._buf).split())
        self._buf = []
        if line:
            self.blocks.append(line)

    def text(self) -> str:
        self._flush()
        return "\n".join(self.blocks)


def _html_to_text(html: bytes) -> str:
    parser = _HtmlTextParser()
    try:
        parser.feed(html.decode("utf-8", "replace"))
        parser.close()
    except Exception:
        return ""
    return parser.text()


def _find_pdf_link(html: bytes, base_url: str) -> Optional[str]:
    """Best-effort discovery of a full-text PDF link on an open publisher
    page (MDPI/Frontiers/PLOS-style landing pages that keep the PDF off the
    aggregator metadata but inline it as <a href=...>)."""
    text = html.decode("utf-8", "replace")
    candidates = []
    for m in re.finditer(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', text, re.I):
        candidates.append(urljoin(base_url, m.group(1)))
    for href in candidates:
        if not href.startswith(("http://", "https://")):
            continue
        return href
    return None


def _pdf_to_text(raw: bytes) -> tuple[str, int]:
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover
        raise RetrievalTransientError(f"pypdf not installed for PDF text: {exc}")
    reader = pypdf.PdfReader(io.BytesIO(raw))
    n = len(reader.pages)
    chunks: list[str] = []
    for pg in reader.pages:
        try:
            chunks.append(pg.extract_text() or "")
        except Exception:
            chunks.append("")
    return "\n\n".join(chunks), n


_ABSTRACT_HEADING_RE = re.compile(r"(?im)^\s*(?:abstract|summary)\s*$")
_REFERENCES_HEADING_RE = re.compile(r"(?im)^\s*(?:references|bibliography|literature cited)\s*$")


def _pdf_multi_article(text: str) -> bool:
    """Return True only on strong structural evidence that one PDF bundles
    multiple articles.

    IMPORTANT: DOI/PMID counts are deliberately NOT used as a stand-alone
    signal. A normal single article's reference list routinely contains many
    distinct DOIs and numeric identifiers, so counting them causes ordinary
    papers to be misclassified as multi-article bundles.

    Strong signals used here are repeated article-level Abstract/Summary
    headings separated by substantial text, or a new Abstract/Summary that
    begins after a References/Bibliography section. This intentionally favors
    false negatives over false positives: uncertain PDFs are still protected
    by the downstream content-identity check, while ordinary single-article
    PDFs must remain eligible for full-text retrieval.
    """
    text = text or ""
    abstracts = [m.start() for m in _ABSTRACT_HEADING_RE.finditer(text)]
    references = [m.start() for m in _REFERENCES_HEADING_RE.finditer(text)]

    # Two article-level abstracts far apart are a strong compiled-PDF signal.
    for i, pos in enumerate(abstracts):
        if any(later - pos >= 2000 for later in abstracts[i + 1:]):
            return True

    # A fresh abstract after a references section strongly indicates that a
    # second article starts after the first one ended.
    for ref_pos in references:
        if any(abs_pos - ref_pos >= 500 for abs_pos in abstracts if abs_pos > ref_pos):
            return True

    return False


def _dspace_fetch(landing_url: str) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Institutional repository (DSpace 7): resolve a handle landing page to
    the deposited file. Returns (text, final_url, content_hash, kind) where
    kind is 'text_bundle' | 'pdf' | None. Prefers the pre-extracted TEXT
    bundle the repository already indexed over parsing the PDF ourselves."""
    parsed = urlparse(landing_url)
    host = parsed.netloc
    m = re.search(r"handle/(\d+/\d+)", parsed.path)
    item_id = None
    if m:
        data = _get_json(f"https://{host}/server/api/pid/find",
                         params={"id": f"hdl:{m.group(1)}"}, timeout=45)
        item_id = (data or {}).get("uuid")
    else:
        m2 = re.search(r"items/([0-9a-f-]{36})", parsed.path)
        if m2:
            item_id = m2.group(1)
    if not item_id:
        return None, None, None, None

    bundles = _get_json(f"https://{host}/server/api/core/items/{item_id}/bundles",
                        params={"size": "50"}, timeout=45)
    bundles = (bundles or {}).get("_embedded", {}).get("bundles", []) or []
    candidates = []
    for b in bundles:
        if b.get("name") not in ("ORIGINAL", "TEXT"):
            continue
        bs = _get_json(f"https://{host}/server/api/core/bundles/{b['uuid']}/bitstreams",
                       params={"size": "50"}, timeout=45)
        for bit in (bs or {}).get("_embedded", {}).get("bitstreams", []) or []:
            content = (bit.get("_links") or {}).get("content", {}).get("href")
            if not content:
                continue
            candidates.append((b.get("name"), bit.get("name") or "", content, bit.get("sizeBytes")))

    # Prefer the repository's own extracted text bundle.
    text_bundle = [c for c in candidates if c[0] == "TEXT"]
    pdf_bundle = [c for c in candidates if c[0] == "ORIGINAL" and (c[1].lower().endswith(".pdf"))]
    pick = (text_bundle + pdf_bundle)
    if pick and pick[0][0] == "TEXT":
        name, content = pick[0][1], pick[0][2]
        raw, _ctype, final = _get_bytes(content, timeout=60)
        if raw:
            return raw.decode("utf-8", "replace"), final, _sha256(raw), "text_bundle"
    if pdf_bundle:
        raw, _ctype, final = _get_bytes(pdf_bundle[0][2], timeout=120)
        if raw and raw[:4] == b"%PDF":
            text, _ = _pdf_to_text(raw)
            if text.strip():
                if _pdf_multi_article(text):
                    return text, final, _sha256(raw), "multi_pdf"
                return text, final, _sha256(raw), "pdf"
    return None, None, None, None


# ---------------------------------------------------------------------------
# Restolvable content stage
# ---------------------------------------------------------------------------

def _fetch_pmc(pmcid: str, email: str) -> tuple[Optional[str], Optional[str], str, str]:
    """NCBI PMC full text via JATS efetch ->
    (abstract_text, body_text, url, content_hash). content_hash = sha256 hex
    of the exact efetch bytes for the PMC article."""
    from src.content_fetcher import _efetch
    bare = pmcid.replace("PMC", "").replace("pmc", "")
    resp = _efetch("pmc", bare, email)
    abstract, body = _jats_abstract_and_body(resp.content)
    url = f"{'https://www.ncbi.nlm.nih.gov/pmc/articles/PMC' + bare}"
    return abstract, body, url, _sha256(resp.content)


def _fetch_pubmed_abstract(pmid: str, email: str) -> tuple[Optional[str], str, str]:
    from src.content_fetcher import _extract_pubmed_abstract, _efetch
    resp = _efetch("pubmed", pmid, email, rettype="abstract")
    abstract = _extract_pubmed_abstract(resp.content)
    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    return abstract, url, _sha256(resp.content)


def _fetch_europepmc_fulltext(cand: Candidate) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Europe PMC fullTextXML when the record has a PMCID/inEPMC full text.
    -> (abstract_text, body_text, url, content_hash); content_hash = sha256 of
    the raw fullTextXML bytes."""
    if not cand.pmcid:
        return None, None, None, None
    pid = cand.pmcid.replace("PMC", "")
    url = f"{_EUROPE_PMC_BASE_URL}/PMC/{pid}/fullTextXML"
    raw, ctype, _ = _get_bytes(url, timeout=90)
    if not raw:
        return None, None, None, None
    abstract, body = _jats_abstract_and_body(raw)
    return abstract, body, url, _sha256(raw)


def _fetch_pdf(url: str) -> tuple[str, str, str, bool]:
    """Fetch a PDF; return (text, final_url, content_hash, multi_article).
    `multi_article` is True when the PDF bundles more than one article and
    must abstain rather than be treated as one paper's body."""
    raw, ctype, final = _get_bytes(url, timeout=120)
    h = _sha256(raw) if raw else ""
    multi = False
    if raw[:4] == b"%PDF" and len(raw) > 5000:
        text, _ = _pdf_to_text(raw)
        if text.strip():
            multi = _pdf_multi_article(text)
            if not multi:
                return text, final, h, False
    return "", final, h, multi


def _fetch_open_access(citation: Citation, cand: Candidate, accord: Optional[str] = None) -> Optional[tuple[str, str, str, bool]]:
    """Fetch full text for a candidate that has an OA location. Returns
    (text, final_url, content_hash, abstain) or None. `abstain` is True when
    the located document is a multi-article PDF (Eduardo's rule: abstain, never
    treat a whole multi-article PDF as one paper's body). Tries pdf_url, landed
    page (DSpace repository, then generic HTML), then a PDF link on the page."""
    def _non_multi(text: str, url: str, h: str):
        if _pdf_multi_article(text):
            return "", url, h, True
        return text, url, h, False

    try:
        if cand.pdf_url:
            text, url, h, multi = _fetch_pdf(cand.pdf_url)
            if multi:
                return "", url, h, True
            if text.strip():
                return text, url, h, False
        if cand.landing_url:
            if "handle/" in cand.landing_url or "items/" in cand.landing_url:
                text, url, h, _kind = _dspace_fetch(cand.landing_url)
                if _kind == "multi_pdf":
                    return "", url or cand.landing_url, h, True
                if text and text.strip():
                    return _non_multi(text, url or cand.landing_url, h)
                return None
            raw, ctype, final = _get_bytes(cand.landing_url, timeout=90)
            if raw[:4] == b"%PDF":
                text, url, h, multi = _fetch_pdf(cand.landing_url)
                if multi:
                    return "", url, h, True
                if text.strip():
                    return text, url, h, False
            text = _html_to_text(raw)
            pdf_link = _find_pdf_link(raw, cand.landing_url)
            if text.strip():
                # prefer the page + inline PDF if present
                if pdf_link:
                    try:
                        ptext, purl, ph, pmulti = _fetch_pdf(pdf_link)
                        if pmulti:
                            return "", purl, ph, True
                        if ptext.strip():
                            return ptext, purl, ph, False
                    except Exception:
                        pass
                return _non_multi(text, final, _sha256(raw))
            if pdf_link:
                try:
                    ptext, purl, ph, pmulti = _fetch_pdf(pdf_link)
                    if pmulti:
                        return "", purl, ph, True
                    if ptext.strip():
                        return ptext, purl, ph, False
                    return None
                except Exception:
                    return None
        return None
    except (RetrievalTransientError, requests.RequestException):
        return None


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------

def _dedupe_candidates(cands: list[Candidate]) -> list[Candidate]:
    """Merge records that share a DOI / normalized title, keeping the richest
    fields (pmcid, pdf_url, landing_url, abstract, oa info) from each."""
    seen: dict[str, Candidate] = {}
    for c in cands:
        key = _norm_compact(c.doi) if c.doi else _norm_compact(c.title or "")
        if not key:
            continue
        if key not in seen:
            seen[key] = c
            continue
        k = seen[key]
        # The source label should name whoever supplied the most useful
        # field (pmcid or abstract), not whoever was seen first.
        provider = "k"
        if not k.pmcid and c.pmcid:
            provider = "c"
        elif not k.abstract and c.abstract:
            provider = "c"
        elif not k.pdf_url and c.pdf_url:
            provider = "c"
        bulk = c if provider == "c" else k
        seen[key] = Candidate(
            source=bulk.source,
            title=k.title or c.title,
            doi=k.doi or c.doi,
            pmid=k.pmid or c.pmid,
            pmcid=k.pmcid or c.pmcid,
            authors=k.authors or c.authors,
            abstract=k.abstract or c.abstract,
            year=k.year or c.year,
            open_access=k.open_access or c.open_access,
            landing_url=k.landing_url or c.landing_url,
            pdf_url=k.pdf_url or c.pdf_url,
            is_oa=k.is_oa or c.is_oa,
            container=k.container or c.container,
        )
    return list(seen.values())


def retrieve_extended(
    citation: Citation,
    contact_email: Optional[str] = None,
    seed_url: Optional[str] = None,
    cache_key: Optional[str] = None,
    cache_dir: Optional[str] = None,
) -> ExtendedRetrievalResult:
    """Extended retrieval cascade.

    1. Gather records from every available aggregator (by DOI or by
       title+first_author+year search).
    2. Identity-check each; dedupe; abstain on ambiguity, not_found on none.
    3. Fetch content: PMC/PubMed/Europe PMC JATS, Unpaywall OA + open
       publisher pages, DSpace repositories, Semantic Scholar abstract.
    4. Return structured fields + final URL + content hash.

    `seed_url` (optional) lets a documented publisher-page / repository URL
    be fetched and identity-checked against its own content -- never trusted
    blindly.
    """
    email = contact_email or _contact_email()

    if cache_key and cache_dir:
        import os
        path = os.path.join(cache_dir, cache_key + ".json")
        os.makedirs(cache_dir, exist_ok=True)
        if os.path.exists(path):
            try:
                d = json.load(open(path, encoding="utf-8"))
                if d.get("status") in ("full_text", "abstract_only"):
                    return ExtendedRetrievalResult(**d)
            except (ValueError, TypeError):
                pass

    # A documented publisher page / repository URL (Eduardo's directive) is
    # authoritative, so fetch and content-identity-check it FIRST; the metadata
    # cascade below still runs if it fails. Never trusted blindly: the document
    # must contain the normalized title + first-author surname + year.
    seed_note = ""
    if seed_url:
        seed_out = _fetch_seed(url=seed_url, citation=citation, email=email,
                               cache_key=cache_key, cache_dir=cache_dir, seen=[])
        if seed_out.status == "full_text":
            return seed_out
        seed_note = seed_out.error_message or seed_out.abstained_reason or ""

    # -------- stage 1: candidates --------
    cands: list[Candidate] = []
    # Identifiers the caller asserted are themselves the identity proof; the
    # synthetic "declared" candidate is always identity-passing so the fast
    # NCBI path stays first in the cascade.
    if citation.pmcid:
        cands.append(Candidate(source="declared", pmcid=citation.pmcid,
                               pmid=citation.pmid, doi=citation.doi))
    elif citation.pmid:
        cands.append(Candidate(source="declared", pmid=citation.pmid,
                               doi=citation.doi))
    if citation.doi:
        for fn in (_openalex_by_doi, _crossref_by_doi, _semanticscholar_by_doi):
            try:
                c = fn(citation.doi)
                if c and (c.title or c.abstract):
                    cands.append(c)
            except (RetrievalTransientError, requests.RequestException):
                continue
    else:
        for fn in (_openalex_search, _crossref_search, _europepmc_search, _semanticscholar_search):
            try:
                cands.extend(fn(citation))
            except (RetrievalTransientError, requests.RequestException):
                continue

    matched: Optional[Candidate] = None
    identity_note = ""
    # A declared-identifier candidate passes identity by construction.
    passing = [c for c in cands if c.source == "declared"
               or candidate_identity(citation, c)[0]]
    passing = _dedupe_candidates(passing)
    passing.sort(key=lambda c: 0 if c.pmcid else (1 if c.pdf_url or c.landing_url else (2 if c.abstract else 3)))

    seen = [{"source": c.source, "doi": c.doi, "title": c.title, "year": c.year,
             "pmcid": c.pmcid, "abstract": bool(c.abstract)} for c in cands]

    if len(passing) == 1:
        matched, identity_note = passing[0], candidate_identity(citation, passing[0])[1]
    elif len(passing) > 1:
        return ExtendedRetrievalResult(
            status="abstained",
            retrieval_status="abstained",
            abstained_reason=("Ambiguous match: " +
                              "; ".join(f"{c.source} {c.doi or c.title}"
                                        for c in passing[:3])),
            candidates_seen=seen,
        )
    if matched is None:
        # A record that matches title+first author but conflicts on the
        # declared publication year is a conflicting match, not a miss --
        # Eduardo's rule: abstain, never auto-match.
        conflicting = []
        for c in cands:
            ok, note = candidate_identity(citation, c)
            if not ok and "year conflict" in note:
                conflicting.append(c)
        if conflicting and not citation.doi:
            return ExtendedRetrievalResult(
                status="abstained",
                retrieval_status="abstained",
                abstained_reason=(
                    "Conflicting record found: title + first author match but "
                    "publication year conflicts "
                    + "; ".join(f"{c.source} ({c.year or '?'}) {c.doi or c.title}"
                                for c in conflicting[:3])
                ),
                candidates_seen=seen,
            )

    # -------- stage 2: content --------
    if matched is None:
        not_found_note = None
        if cands:
            not_found_note = ("no candidate passed identity "
                              f"(saw {len(cands)} records, refused to auto-match)")
        if seed_note:
            not_found_note = (not_found_note + "; " if not_found_note else "") + f"seed: {seed_note}"
        return ExtendedRetrievalResult(
            status="not_found",
            retrieval_status="not_found",
            identity_note=identity_note or not_found_note,
            candidates_seen=seen,
        )

    # content data assembly, ordered by strength
    result = ExtendedRetrievalResult(
        status="not_found", retrieval_status="not_found",
        matched_candidate={"source": matched.source, "doi": matched.doi,
                           "title": matched.title, "year": matched.year},
        identity_note=identity_note, candidates_seen=seen,
    )

    # (a) PMC full text
    if matched.pmcid:
        try:
            abstract, body, url, chash = _fetch_pmc(matched.pmcid, email)
            if body:
                result.abstract_text = abstract
                result.body_text = body
                result.retrieval_source = "pmc"
                result.retrieval_status = "full"
                result.structure_status = "resolved"
                result.final_url = url
                result.content_hash = chash
                result.status = "full_text"
                return _finalize(result, cache_key, cache_dir)
        except (RetrievalTransientError, requests.RequestException):
            pass

    # (b) Europe PMC fullTextXML
    try:
        abstract, body, url, chash = _fetch_europepmc_fulltext(matched)
        if body:
            result.abstract_text = abstract
            result.body_text = body
            result.retrieval_source = "europepmc"
            result.retrieval_status = "full"
            result.structure_status = "resolved"
            result.final_url = url
            result.content_hash = chash
            result.status = "full_text"
            return _finalize(result, cache_key, cache_dir)
        if abstract and not result.abstract_text:
            result.abstract_text = abstract
            result.retrieval_source = "europepmc"
            result.retrieval_status = "abstract_only"
            result.structure_status = "not_applicable"
            result.final_url = url
            result.content_hash = chash
            result.status = "abstract_only"
    except (RetrievalTransientError, requests.RequestException):
        pass

    # (c) OA full text (Unpaywall -> publisher / repository pages)
    oa_error = ""
    multi_pdf_seen = None  # (url, content_hash); defer abstention until fallbacks are exhausted
    if matched.doi:
        try:
            locations = _unpaywall_locations(matched.doi)
        except (RetrievalTransientError, requests.RequestException) as exc:
            locations = []
            oa_error = f"unpaywall transient: {exc}"
        for loc in locations:
            probe = Candidate(
                source="unpaywall",
                pdf_url=loc.get("url_for_pdf") or loc.get("url"),
                landing_url=loc.get("url_for_landing_page") or loc.get("url"),
                is_oa=bool(loc.get("is_oa")),
            )
            try:
                fetched = _fetch_open_access(citation, probe)
            except (RetrievalTransientError, requests.RequestException) as exc:
                oa_error = oa_error or f"{loc.get('url')}: {exc}"
                continue
            if not fetched:
                continue
            text, url, h, multi = fetched
            if multi:
                # Do not treat the bundle as one paper, but do not abandon the
                # case either. Try the remaining OA locations and then the
                # aggregator/PubMed abstract fallbacks. Abstain only if no
                # safe evidence can be recovered at all.
                multi_pdf_seen = (url or loc.get("url"), h)
                continue
            ok, note = _content_identity(citation, text)
            if not ok:
                continue
            abs_txt, body_txt, sstatus = _split_abstract_body_loose(text)
            result.abstract_text = abs_txt
            result.body_text = body_txt
            result.retrieval_source = ("dspace_repository" if "dspace" in (url or "")
                                       or "handle/" in (url or "") else "publisher")
            result.retrieval_status = "full"
            result.structure_status = sstatus
            result.final_url = url
            result.content_hash = h
            result.identity_note = (result.identity_note or "") + " | " + note
            result.status = "full_text"
            return _finalize(result, cache_key, cache_dir)

    # (d) abstract from the aggregator (Semantic Scholar / Crossref / EPMC)
    if matched.abstract and result.abstract_text is None:
        result.abstract_text = matched.abstract
        result.retrieval_source = matched.source
        result.retrieval_status = "abstract_only"
        result.structure_status = "not_applicable"
        # Metadata abstracts: the canonical content is the abstract bytes; the
        # authoritative location is the DOI URL when a DOI is available.
        if matched.doi:
            result.final_url = f"https://doi.org/{matched.doi}"
        else:
            result.final_url = ""
        result.content_hash = _sha256(matched.abstract.encode("utf-8"))
        result.status = "abstract_only"
        return _finalize(result, cache_key, cache_dir)

    # (e) PubMed abstract (last resort content for a matched record)
    if matched.pmid and result.abstract_text is None:
        try:
            abstract, url, chash = _fetch_pubmed_abstract(matched.pmid, email)
            if abstract:
                result.abstract_text = abstract
                result.retrieval_source = "pubmed"
                result.retrieval_status = "abstract_only"
                result.structure_status = "not_applicable"
                result.final_url = url
                result.content_hash = chash
                result.status = "abstract_only"
            else:
                result.status = "not_found"
                result.retrieval_status = "not_found"
        except (RetrievalTransientError, requests.RequestException):
            pass

    if result.status == "not_found" and multi_pdf_seen:
        url, h = multi_pdf_seen
        return ExtendedRetrievalResult(
            status="abstained",
            retrieval_status="abstained",
            abstained_reason=("Multi-article PDF located "
                              f"({url}); refused to treat the whole bundle "
                              "as one paper's body, and no safe fallback "
                              "evidence was available."),
            final_url=url,
            content_hash=h,
            candidates_seen=seen,
        )
    if result.status == "not_found" and oa_error:
        result.identity_note = ((result.identity_note or "") + " | " + oa_error)
    return _finalize(result, cache_key, cache_dir)


def _fetch_seed(url: str, citation: Citation, email: str,
                cache_key, cache_dir, seen) -> ExtendedRetrievalResult:
    """Fetch a documented publisher-page/repository URL, then identity-check
    the document against its own content (never trusted blindly)."""
    try:
        raw, ctype, final = _get_bytes(url, timeout=150)
    except (RetrievalTransientError, requests.RequestException) as exc:
        return ExtendedRetrievalResult(status="error", retrieval_status="error",
                                       error_message=f"seed URL fetch failed: {exc}",
                                       candidates_seen=seen or [])
    h = _sha256(raw)
    text = ""
    multi_seed = False
    if raw[:4] == b"%PDF" and len(raw) > 5000:
        text, _n = _pdf_to_text(raw)
        multi_seed = _pdf_multi_article(text)
    elif ctype and "html" in ctype.lower():
        text = _html_to_text(raw)
        pdf_link = _find_pdf_link(raw, url)
        if pdf_link:
            try:
                ptext, purl, ph, pmulti = _fetch_pdf(pdf_link)
                if pmulti:
                    return ExtendedRetrievalResult(
                        status="abstained", retrieval_status="abstained",
                        abstained_reason=("Multi-article PDF located at seed "
                                          f"{purl or url}; refused to treat the whole "
                                          "bundle as one paper's body."),
                        final_url=purl or url, content_hash=ph,
                        candidates_seen=seen or [])
                if ptext.strip():
                    text, final, h = ptext, purl, ph
            except Exception:
                pass
    else:
        try:
            text = raw.decode("utf-8", "replace")
        except Exception:
            text = ""
    if not text.strip():
        return ExtendedRetrievalResult(status="error", retrieval_status="error",
                                       error_message="seed URL returned no extractable text",
                                       candidates_seen=seen or [])
    if multi_seed:
        return ExtendedRetrievalResult(status="abstained", retrieval_status="abstained",
                                       abstained_reason=("Multi-article PDF at seed "
                                                         f"{final or url}; refused to treat "
                                                         "the whole bundle as one paper's body."),
                                       final_url=final, content_hash=h,
                                       candidates_seen=seen or [])
    ok, note = _content_identity(citation, text)
    if not ok:
        return ExtendedRetrievalResult(status="abstained", retrieval_status="abstained",
                                       abstained_reason=note, final_url=final,
                                       content_hash=h, candidates_seen=seen or [])
    abs_txt, body_txt, sstatus = _split_abstract_body_loose(text)
    result = ExtendedRetrievalResult(
        status="full_text",
        abstract_text=abs_txt, body_text=body_txt,
        retrieval_source="publisher_page",
        retrieval_status="full", structure_status=sstatus,
        final_url=final, content_hash=h, identity_note=note,
        candidates_seen=seen or [],
    )
    return _finalize(result, cache_key, cache_dir)


def _finalize(result: ExtendedRetrievalResult, cache_key, cache_dir):
    if cache_key and cache_dir and result.status in ("full_text", "abstract_only"):
        import os
        path = os.path.join(cache_dir, cache_key + ".json")
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "status": result.status, "abstract_text": result.abstract_text,
                "body_text": result.body_text, "retrieval_source": result.retrieval_source,
                "retrieval_status": result.retrieval_status,
                "structure_status": result.structure_status,
                "final_url": result.final_url, "content_hash": result.content_hash,
                "matched_candidate": result.matched_candidate,
                "identity_note": result.identity_note,
            }, f, ensure_ascii=False, indent=2)
    return result