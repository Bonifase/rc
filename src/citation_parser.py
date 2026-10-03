"""
Parses citation markers out of a sentence and expands them into reference
numbers -- e.g. "...[3][8]." -> [3, 8], "...[4,5]." -> [4, 5],
"...[3-5]." -> [3, 4, 5].

Per the integration note: "expand ranges before building the pairs --
[3-5] is three references, not one." This module owns that expansion so
pipeline.py can build one (sentence, source) pair per reference number
without re-deriving the parsing logic.
"""
import re

# Matches a single bracket group, e.g. "[3]", "[4,5]", "[3-5]", "[3,5-7]".
# Accepts a hyphen or en/em dash as the range separator, since sentences
# copied from PDFs commonly use either.
_BRACKET_GROUP = re.compile(r"\[([\d,\s\u2013\u2014-]+)\]")
_RANGE = re.compile(r"^(\d+)\s*[\u2013\u2014-]\s*(\d+)$")


def parse_citation_markers(text: str) -> list[int]:
    """
    Extract every reference number cited in a sentence, across all bracket
    groups, with ranges expanded. Duplicates are dropped; order of first
    appearance is preserved (not numeric order), since that's generally
    more useful for debugging/logging what was actually parsed.

    Returns an empty list if no bracket markers are found -- callers decide
    what that means (e.g. "this sentence has no citations to check").
    """
    seen: list[int] = []

    for match in _BRACKET_GROUP.finditer(text):
        for part in match.group(1).split(","):
            part = part.strip()
            if not part:
                continue

            range_match = _RANGE.match(part)
            if range_match:
                start, end = int(range_match.group(1)), int(range_match.group(2))
                lo, hi = (start, end) if start <= end else (end, start)
                for n in range(lo, hi + 1):
                    if n not in seen:
                        seen.append(n)
                continue

            if part.isdigit():
                n = int(part)
                if n not in seen:
                    seen.append(n)
            # Silently skip anything that's neither a plain number nor a
            # range (e.g. stray text that happens to sit in brackets) --
            # this is a best-effort parser, not a validator.

    return seen
