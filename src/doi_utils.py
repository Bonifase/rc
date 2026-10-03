"""
Shared DOI-extraction helper. Both text_parser.py (content pipeline) and
src/metadata/extraction.py (metadata module) need to pull a bare DOI out of
raw text; this is the one place that logic lives so the two modules don't
duplicate or drift from each other, while still not requiring either to
import the other's internals.
"""
import re
from typing import Optional

_DOI_URL = re.compile(r"https?://doi\.org/([^\s]+)")
_BARE_DOI = re.compile(r"(10\.\d{4,9}/[^\s,;]+)")


def extract_doi(text: str) -> Optional[str]:
    """
    Pull a bare DOI (e.g. '10.1038/nature17164') out of free text, preferring
    an explicit https://doi.org/ URL over a bare '10.xxxx/...' pattern found
    anywhere in the string. Returns None if nothing DOI-shaped is found.
    """
    url_match = _DOI_URL.search(text)
    if url_match:
        return url_match.group(1).strip().rstrip(".,;")

    bare_match = _BARE_DOI.search(text)
    if bare_match:
        return bare_match.group(1).strip().rstrip(".,;")

    return None
