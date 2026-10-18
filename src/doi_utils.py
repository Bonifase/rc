"""
Shared DOI-extraction helper. Both text_parser.py (content pipeline) and
src/metadata/extraction.py (metadata module) need to pull a bare DOI out of
raw text; this is the one place that logic lives so the two modules don't
duplicate or drift from each other, while still not requiring either to
import the other's internals.
"""
import re
from typing import Optional, Tuple

_DOI_URL = re.compile(r"https?://(?:dx\.)?doi\.org/([^\s]+)")
_BARE_DOI = re.compile(r"(10\.\d{4,9}/[^\s,;]+)")

# A DOI that a word processor split with a space or line break, e.g.
# "10.1016/j.ygcen. 2008.04.009". A DOI never ends in ".", "-", "/" or "_",
# so when the matched part ends in one of those and the next token starts
# with a digit, the two pieces are one DOI.
# Same-line whitespace only, so a DOI ending a line is never glued to the
# next line (for example the number of the next bibliography entry).
_SPLIT_CONTINUATION = re.compile(r"[ \t\u00a0]+([0-9][^\s,;]*)")
_BARE_NUMBER = re.compile(r"^\d+\.?$")
_SPLIT_ENDINGS = (".", "-", "/", "_")


def _clean(doi: str) -> str:
    return doi.strip().rstrip(".,;")


def find_doi(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (doi, doi_as_written).

    doi is the bare DOI (e.g. '10.1038/nature17164'), preferring an explicit
    https://doi.org/ URL over a bare '10.xxxx/...' pattern; None if nothing
    DOI-shaped is found.

    doi_as_written is None unless the DOI had to be re-joined because the
    text splits it with whitespace; then it is the literal written form
    (e.g. '10.1016/j.ygcen. 2008.04.009'), so the metadata axis can report
    the defect as written instead of reporting a truncated DOI.
    """
    match = _DOI_URL.search(text) or _BARE_DOI.search(text)
    if not match:
        return None, None

    raw = match.group(1).strip()
    if raw.endswith(_SPLIT_ENDINGS):
        continuation = _SPLIT_CONTINUATION.match(text, match.end(1))
        if continuation and not _BARE_NUMBER.match(continuation.group(1)):
            joined = _clean(raw + continuation.group(1))
            written = text[match.start(1):continuation.end(1)].strip().rstrip(".,;")
            return joined, written
    return _clean(raw), None


def extract_doi(text: str) -> Optional[str]:
    """
    Pull a bare DOI (e.g. '10.1038/nature17164') out of free text, preferring
    an explicit https://doi.org/ URL over a bare '10.xxxx/...' pattern found
    anywhere in the string. A DOI split by whitespace is re-joined (see
    find_doi). Returns None if nothing DOI-shaped is found.
    """
    return find_doi(text)[0]
