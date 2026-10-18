"""
3a — STRUCTURED EXTRACTION (per the spec: build first, everything else
depends on it).

Turns a raw bibliography entry string into a DeclaredReference record:
authors (family/given, structured), year, title, journal, volume, issue,
pages, doi, issn. Fields not found are None, never empty strings -- "the
author is missing" and "the author field is empty" have to stay
distinguishable per the spec.

Citation-marker extraction (per sentence, every marker, ranges expanded)
already exists in src/citation_parser.py and is reused here rather than
reimplemented -- it already handles every format the spec lists ([3],
[3][8], [3,8], [3, 8], [3-5], [3\u20135]).

HONEST LIMITATION: reference-string parsing is inherently style-dependent.
This parser targets the APA-style pattern actually used by every real
reference in this project's own sample/test documents -- e.g.

    Lien, S., Koop, B. F., ... Davidson, W. S. (2016). The Atlantic salmon
    genome provides insights into rediploidization. Nature, 533(7602),
    200-205. https://doi.org/10.1038/nature17164

-- "Family, F. F., & Family2, F. (Year). Title. Journal, Volume(Issue),
Pages. DOI". It is not a general-purpose citation parser (Vancouver, MLA,
and Chicago styles will parse partially at best). Any field the regex
can't confidently extract is left None rather than guessed at, per the
spec's "null vs empty string" rule -- a downstream check comparing a None
declared field against a real actual field should treat that as "cannot
compare," not as a mismatch.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from src.citation_parser import parse_citation_markers as _expand_markers
from src.doi_utils import find_doi

# "Faught, E." / "Vijayan, M. M." / "van der Berg, J.-P." -- a family name
# (allowing internal hyphens/apostrophes and a lowercase-particle prefix like
# "van der") followed by one or more capitalized initials.
_AUTHOR = re.compile(
    r"([A-Z][A-Za-z\u00C0-\u00FF'\-]+(?:\s(?:van|von|der|de|la|el)\s[A-Za-z\u00C0-\u00FF'\-]+)?"
    r"(?:\s[A-Z][A-Za-z\u00C0-\u00FF'\-]+)?),\s*((?:[A-Z]\.\-?\s*)+)"
)

# "(2018)" or "(2018a)" -- the year, immediately after the author list.
# Stray spaces inside the parentheses ("(2010 )", "( 2016)") are tolerated:
# they are typing noise, not a different year.
_YEAR = re.compile(r"\(\s*(\d{4})[a-z]?\s*\)")

# Name-first author lists ("M. Codina, D. Garcia de la Serrana, et al" or
# "Emilio J. Velez, Esmail Lutfi"): the entry number that some lists keep in
# front of the first author, the "et al." / "&" / "and" connectors, the
# initials-only chunk that marks APA style, and the lowercase particles that
# belong to the family name.
_LEADING_NUMBER = re.compile(r"^\s*(?:\[\d+\]|\d+[.)])\s+")
_INITIALS_ONLY = re.compile(r"^(?:[A-Z\u00C0-\u00DE]\.(?:\s*-?\s*[A-Z\u00C0-\u00DE]\.)*\s*)+$")
_CONNECTOR = re.compile(r"^(?:&|and)\s+", re.IGNORECASE)
_ET_AL = re.compile(r"^et\s+al\.?$|^\.\.\.$|^\u2026$", re.IGNORECASE)
_PARTICLES = {"van", "von", "der", "den", "de", "la", "le", "el", "del", "della", "da", "das", "do", "dos", "du", "di", "y"}

# The tail pattern: "Journal Name, Volume(Issue), Pages." possibly followed
# by a DOI/URL. Volume is required; issue and pages are optional so entries
# missing one still match on the rest.
_JOURNAL_TAIL = re.compile(
    r"([A-Z][^.]*?),\s*"           # journal name (up to the next period)
    r"(\d+)\s*"                     # volume (a space before "(issue)" is common)
    r"(?:\((\d+[A-Za-z]?)\))?"     # optional (issue)
    r"(?:,\s*([\d]+\s*[\u2013\u2014-]\s*[\d]+|[\d]+))?"  # optional pages (range or single)
    r"\.?\s*$"
)

_TRUNCATION_MARKERS = re.compile(r"\.\.\.|\bet al\.?\b", re.IGNORECASE)


@dataclass
class Author:
    family: Optional[str] = None
    given: Optional[str] = None


@dataclass
class DeclaredReference:
    """
    What the paper's own reference list says. Compare against an
    ActualReference (src/metadata/crossref_metadata.py) to find mismatches.
    Fields not found are None, never "".
    """
    reference_id: int
    raw: str
    authors: list[Author] = field(default_factory=list)
    year: Optional[int] = None
    title: Optional[str] = None
    journal: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    doi: Optional[str] = None
    issn: Optional[str] = None  # essentially always None from raw text; kept for schema symmetry with ActualReference
    authors_truncated: bool = False  # raw text contained "..." or "et al." near the author list
    doi_as_written: Optional[str] = None  # set only when the written DOI is split by whitespace


def _strip_doi_and_urls(text: str) -> str:
    """Removes the DOI/URL tail so it doesn't interfere with journal/pages parsing."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\bdoi:\s*10\.\d{4,9}/\S+", "", text, flags=re.IGNORECASE)
    return text.strip()


def _author_chunks(authors_segment: str) -> list[str]:
    """Comma-separated pieces of the author list, connectors and 'et al.' removed."""
    segment = _LEADING_NUMBER.sub("", authors_segment).strip().rstrip(",").strip()
    chunks = []
    for chunk in re.split(r",\s*", segment):
        chunk = _CONNECTOR.sub("", chunk.strip()).strip()
        chunk = re.sub(r"\s+et\s+al\.?$", "", chunk, flags=re.IGNORECASE).strip()
        if chunk and not _ET_AL.match(chunk):
            chunks.append(chunk)
    return chunks


def _is_apa_style(chunks: list[str]) -> bool:
    """APA lists alternate 'Family' and 'I. I.': the second piece is initials only."""
    return len(chunks) >= 2 and bool(_INITIALS_ONLY.match(chunks[1]))


def _split_name_first(name: str) -> Optional[Author]:
    """'G. M. Weber' -> Weber / G. M.; 'Emilio J. Velez' -> Velez / Emilio J.;
    'D. Garcia de la Serrana' -> Garcia de la Serrana / D."""
    tokens = name.split()
    if len(tokens) < 2:
        return None
    # The family name starts at the first token that is neither an initial
    # nor a given name: walk back from the last token over lowercase particles
    # and capitalised words that follow a particle.
    family_start = len(tokens) - 1
    while family_start > 1 and tokens[family_start - 1].lower() in _PARTICLES:
        family_start -= 1
    # "D. Garcia de la Serrana": when the given name is written as initials,
    # the capitalised word right before the particles is part of the family
    # name. With a full given name ("Juan Carlos de la Cruz") it is not.
    if (family_start < len(tokens) - 1 and family_start > 1
            and not tokens[family_start - 1].endswith(".")
            and tokens[family_start - 2].endswith(".")):
        family_start -= 1
    given = " ".join(tokens[:family_start]).strip()
    family = " ".join(tokens[family_start:]).strip()
    if not given or not family or family.endswith("."):
        return None
    return Author(family=family, given=given)


def _parse_authors(authors_segment: str) -> list[Author]:
    """
    Two author-list shapes are read:

    - APA, family first: "Cleveland, B. M., & Weber, G. M." (the original
      pattern of this module, unchanged);
    - name first, as Elicit and several journals write them:
      "B. Cleveland, G. M. Weber" or "Emilio J. Velez, Esmail Lutfi, et al".

    The shape is decided once per list, from the second comma-separated
    piece: initials only means APA. Reading a name-first list with the APA
    pattern pairs each family name with the next author's initials and loses
    authors, which produced false "author count" findings.
    """
    chunks = _author_chunks(authors_segment)
    if not chunks:
        return []
    if _is_apa_style(chunks):
        authors = []
        for match in _AUTHOR.finditer(_LEADING_NUMBER.sub("", authors_segment)):
            family = match.group(1).strip()
            given = match.group(2).strip()
            authors.append(Author(family=family, given=given))
        return authors

    authors = []
    for chunk in chunks:
        author = _split_name_first(chunk)
        if author is not None:
            authors.append(author)
    return authors


def parse_reference_entry(reference_id: int, raw: str) -> DeclaredReference:
    """
    Parses one raw bibliography entry into a DeclaredReference. Never raises
    on unparseable input -- worst case, every field beyond reference_id/raw/
    doi is None, and the raw string is always preserved so nothing is lost.
    """
    raw = raw.strip()
    doi, doi_as_written = find_doi(raw)
    body = _strip_doi_and_urls(raw)

    year_match = _YEAR.search(body)
    year = int(year_match.group(1)) if year_match else None

    authors: list[Author] = []
    authors_truncated = False
    title: Optional[str] = None
    journal: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None

    if year_match:
        authors_segment = body[: year_match.start()]
        authors = _parse_authors(authors_segment)
        authors_truncated = bool(_TRUNCATION_MARKERS.search(authors_segment))

        after_year = body[year_match.end():].strip().lstrip(".").strip()
        tail_match = _JOURNAL_TAIL.search(after_year)
        if tail_match:
            journal = tail_match.group(1).strip()
            volume = tail_match.group(2)
            issue = tail_match.group(3)
            pages = tail_match.group(4).strip() if tail_match.group(4) else None
            title = after_year[: tail_match.start()].strip().rstrip(".").strip() or None
        else:
            # Couldn't confidently split journal/volume/pages -- leave those
            # None rather than guess. If there's at least a trailing
            # ". Something" segment, treat everything before the last
            # sentence as the title; otherwise leave title None too.
            parts = [p.strip() for p in after_year.split(".") if p.strip()]
            title = parts[0] if parts else None
    else:
        # No parseable year -- author/title/journal splitting has no
        # reliable anchor point left, so leave those None. DOI (if present)
        # is still captured above.
        pass

    return DeclaredReference(
        reference_id=reference_id,
        raw=raw,
        authors=authors,
        year=year,
        title=title,
        journal=journal,
        volume=volume,
        issue=issue,
        pages=pages,
        doi=doi,
        issn=None,
        authors_truncated=authors_truncated,
        doi_as_written=doi_as_written,
    )


def expand_citation_markers(sentence_text: str) -> list[int]:
    """
    Every reference number a sentence cites, across all bracket groups, with
    ranges expanded ([3-5] -> [3, 4, 5], not one entry). Thin wrapper around
    src/citation_parser.py so callers of the metadata module only need to
    import from src.metadata.extraction, not reach into the content
    pipeline's modules directly.
    """
    return _expand_markers(sentence_text)


def build_declared_references(reference_entries: list[dict]) -> list[DeclaredReference]:
    """
    Bridges the content pipeline's reference_list shape
    ({"number": 1, "doi": "...", "raw": "..." (optional)}) into the metadata
    module's DeclaredReference records.

    "raw" is only present when the input came from parsed free text
    (parse_pasted_text / /api/analyze-text) -- direct-JSON input
    (/api/analyze) has no bibliography string to parse fields out of.
    Degrades gracefully in that case: a DeclaredReference with just
    reference_id/doi and every other field None, rather than failing. Checks
    that need journal/volume/authors/etc. simply have nothing to check for
    that reference and produce no findings for it -- resolution can still
    run off the DOI alone.
    """
    declared = []
    for entry in reference_entries:
        number = int(entry["number"])
        raw = entry.get("raw")
        if raw:
            declared.append(parse_reference_entry(number, raw))
        else:
            declared.append(DeclaredReference(reference_id=number, raw="", doi=entry.get("doi")))
    return declared
