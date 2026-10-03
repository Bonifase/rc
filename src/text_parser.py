"""
Parses pasted academic text into the structured input the pipeline expects:
a reference list (number -> DOI) and a list of sentences with citation markers.

The user pastes their full paper text (introduction, discussion, etc.) with
the reference list appended at the end. This module:
1. Finds the reference list block (lines starting with [N])
2. Extracts DOIs from each reference line
3. Identifies the body text above the references
4. Splits the body into sentences and extracts citation markers
"""
import re
from dataclasses import dataclass
from typing import Optional

# Matches a reference line: starts with [N] and contains a DOI anywhere
_REF_LINE = re.compile(
    r"^\s*\[(\d+)\]\s+.+?"            # [N] followed by some text
    r"(https?://doi\.org/[^\s]+)"       # https://doi.org/...
    r"|"
    r"^\s*\[(\d+)\]\s+.+?"            # [N] followed by some text
    r"(10\.\d{4,9}/[^\s]+)",           # bare DOI: 10.xxxx/...
)

# Matches the common bibliography entry prefixes used by manuscripts:
#   [1] ...
#   1. ...
#   1) ...
#   Author, A. ...
#   Author et al. (2022) ...
_REF_LINE_START = re.compile(r"^\s*\[(\d+)\]\s")
_REF_NUMBERED_LINE_START = re.compile(r"^\s*(\d+)\s*(?:[\.)]|\.)\s")
_REF_ROMAN_LINE_START = re.compile(r"^\s*([ivxlcdm]+)\s*(?:[\.)]|\.)\s", re.IGNORECASE)
_REF_AUTHOR_LED_START = re.compile(
    r"^\s*(?:[A-Z][A-Za-z'’.-]+(?:,\s*[A-Z]\.)*(?:\s+(?:et\s+al\.|and|&)\s*(?:[A-Z][A-Za-z'’.-]+)?)?|"
    r"[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+)*,\s*(?:[A-Z]\.?\s*)+(?:[A-Z][A-Za-z'’.-]+)?)"
)

# Common reference section headers
_REF_SECTION_MARKERS = re.compile(
    r"^\s*(references|bibliography|works cited|literature cited|citations)\s*$",
    re.IGNORECASE,
)

# Sentence splitter: splits on sentence-ending punctuation followed by whitespace
# or end of string. Keeps the period with the sentence. Parenthetical year
# citations such as "Smith et al. (2020)" must remain in the same sentence,
# which means we do not split before a following open parenthesis.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[])")

# A numbered section heading, e.g. "2. Ligand specificity of the teleost
# mineralocorticoid receptor". Treated as a hard boundary: the heading number
# must never be glued onto the tail of the previous sentence (before this fix
# the splitter stopped at "2." but swallowed the heading number because the
# following char "2" isn't uppercase, then split on the heading's first word).
_HEADING_LINE = re.compile(r"^\d+\.\s+[A-Z]")

# Detects citation markers: [3], [4,5], [3-5], [3,5-7], etc.
_CITATION_MARKER = re.compile(r"\[[\d,\s\u2013\u2014\-]+\]")
_AUTHOR_YEAR_MARKER = re.compile(
    r"([A-Z][A-Za-z'’.-]+(?:,\s*[A-Z]\.)*(?:\s+(?:et\s+al\.?|and|&)\s*(?:[A-Z][A-Za-z'’.-]+)?)?)\s*\((\d{4})\)"
)


@dataclass
class ParseResult:
    """
    The structured output of parsing pasted text.
    Matches the shape the pipeline's /api/analyze endpoint already expects.
    """
    reference_list: list[dict]  # [{"number": 1, "doi": "10.xxxx/..."}, ...]
    sentences: list[dict]       # [{"id": "s1", "text": "..."}, ...]
    warnings: list[str]         # non-fatal issues (e.g. reference without DOI)


def _extract_doi_from_line(line: str) -> Optional[str]:
    """Pull a DOI out of a reference line, preferring https://doi.org/ URLs.
    Returns a bare DOI (e.g. '10.1056/NEJMoa1800389') suitable for Crossref/NCBI APIs."""
    match = _REF_LINE.search(line)
    if match:
        raw = (match.group(2) or match.group(4)).strip()
        # Strip https://doi.org/ or http://doi.org/ prefix to get the bare DOI
        raw = re.sub(r"^https?://doi\.org/", "", raw)
        return raw

    # Fallback: look for a bare DOI pattern anywhere in the line
    bare = re.search(r"(10\.\d{4,9}/[^\s,;]+)", line)
    if bare:
        return bare.group(1).strip().rstrip(".,;")

    return None


def _has_citation_markers(text: str) -> bool:
    """Check whether a sentence contains any bracket citation markers."""
    return bool(_CITATION_MARKER.search(text))


def _looks_like_reference_entry_start(line: str) -> bool:
    """Return True when a reference-section line appears to start a new bibliography entry."""
    stripped = line.strip()
    if not stripped:
        return False

    if _REF_LINE_START.match(stripped) or _REF_NUMBERED_LINE_START.match(stripped) or _REF_ROMAN_LINE_START.match(stripped):
        return True

    # Author-led bibliography entries are common in manuscript reference lists.
    # They often look like "Manneken, J. D., ... (2022)" or "Smith et al. (2020) ...".
    if _REF_AUTHOR_LED_START.match(stripped):
        if re.search(r"\b(19|20)\d{2}\b", stripped) or any(token in stripped.lower() for token in (" et al.", " and ", " & ", ",", "doi", "https://")):
            return True

    return False


def _extract_reference_number(line: str) -> Optional[int]:
    """Extract a numeric reference label when the bibliography entry begins with [N] or N."""
    stripped = line.strip()
    if not stripped:
        return None

    match = _REF_LINE_START.match(stripped)
    if match:
        return int(match.group(1))

    match = _REF_NUMBERED_LINE_START.match(stripped)
    if match:
        return int(match.group(1))

    match = _REF_ROMAN_LINE_START.match(stripped)
    if match:
        try:
            return int(match.group(1), 36)
        except ValueError:
            return None

    return None


def _split_into_sentences(text: str) -> list[str]:
    """
    Split body text into sentences. Numbered section headings ("2. Ligand
    specificity ...") are hard boundaries: a heading starts a fresh block and
    its own line is not part of any sentence. Everything else is split by the
    period/!? + uppercase/bracket heuristic, with an extra pass to merge
    author-name fragments such as "Smith, J.," and "(2020)" back together.
    """
    sentences: list[str] = []
    buffer: list[str] = []

    def flush():
        if buffer:
            chunk = " ".join(buffer)
            candidates = [s.strip() for s in _SENTENCE_SPLIT.split(chunk) if s.strip()]
            merged: list[str] = []
            for candidate in candidates:
                if not merged:
                    merged.append(candidate)
                    continue
                prev = merged[-1]
                if candidate.startswith("(") and (re.search(r"\bet al\.?$", prev, re.IGNORECASE) or re.search(r"\b[A-Z]$", prev)):
                    merged[-1] = f"{prev} {candidate}"
                elif re.search(r"\b[A-Z]\.$", prev) or prev.endswith(",") or re.search(r"\bet al\.?$", prev, re.IGNORECASE):
                    merged[-1] = f"{prev} {candidate}"
                else:
                    merged.append(candidate)
            sentences.extend(merged)
            buffer.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if _HEADING_LINE.match(stripped):
            flush()
            continue
        if stripped:
            buffer.append(stripped)

    flush()
    return sentences


def parse_pasted_text(text: str) -> ParseResult:
    """
    Parse the user's pasted text into reference_list + sentences.

    Strategy:
    1. Find where the references section starts (explicit header or first [N] line).
    2. Everything above that is body text; everything below is references.
    3. Group reference lines into blocks (one block per [N] entry, which may span
       several lines), then extract a DOI from each block.
    4. Split body into sentences; keep only those with citation markers.
    """
    lines = text.split("\n")
    warnings: list[str] = []

    ref_start_idx = None  # type: Optional[int]

    for i, line in enumerate(lines):
        # Explicit "References" / "Bibliography" header
        if _REF_SECTION_MARKERS.match(line):
            ref_start_idx = i + 1
            break

    if ref_start_idx is None:
        # No explicit header. Find the first line that starts a common reference entry.
        # Manuscripts may use bracketed numbering, numbered labels, or author-led entries.
        for i, line in enumerate(lines):
            if _looks_like_reference_entry_start(line):
                ref_start_idx = i
                break

    if ref_start_idx is None:
        body_text = text.strip()
        raw_sentences = _split_into_sentences(body_text)
        sentences: list[dict] = []
        for s in raw_sentences:
            if _has_citation_markers(s) or _AUTHOR_YEAR_MARKER.search(s):
                sentences.append({"id": f"s{len(sentences) + 1}", "text": s})
        return ParseResult(
            reference_list=[],
            sentences=sentences,
            warnings=["No references section or [N] entries found in the text; author-year citations were parsed from the body text."],
        )

    body_text = "\n".join(lines[:ref_start_idx]).strip()
    ref_lines = lines[ref_start_idx:]

    # --- Parse references (blocks can start with [N], N., or author-led bibliographic entries) ---
    reference_list: list[dict] = []
    blocks: list[tuple[int, list[str]]] = []
    current_num: Optional[int] = None
    current_block: list[str] = []
    next_auto_number = 1

    for line in ref_lines:
        if _looks_like_reference_entry_start(line):
            if current_block:
                blocks.append((current_num if current_num is not None else next_auto_number - 1, current_block))
            current_num = _extract_reference_number(line)
            if current_num is None:
                current_num = next_auto_number
                next_auto_number += 1
            else:
                next_auto_number = max(next_auto_number, current_num + 1)
            current_block = [line]
        elif current_block:
            current_block.append(line)

    if current_block:
        blocks.append((current_num if current_num is not None else next_auto_number - 1, current_block))

    for num, block in blocks:
        joined = " ".join(block).strip()
        if not joined:
            continue
        doi = _extract_doi_from_line(joined)
        entry = {"number": num, "raw": joined}
        if doi:
            entry["doi"] = doi
        else:
            # No identifier: keep the entry so the pipeline can locate the
            # paper by title + first author + year (extended retrieval).
            warnings.append(
                f"Reference [{num}] found with no DOI; it will be located by title, first author, and year."
            )
        reference_list.append(entry)

    # --- Parse sentences ---
    raw_sentences = _split_into_sentences(body_text)
    sentences: list[dict] = []
    sid = 0
    for s in raw_sentences:
        if _has_citation_markers(s) or _AUTHOR_YEAR_MARKER.search(s):
            sid += 1
            sentences.append({"id": f"s{sid}", "text": s})

    if not sentences:
        warnings.append("No sentences with citation markers ([N]) were found in the body text.")

    return ParseResult(
        reference_list=reference_list,
        sentences=sentences,
        warnings=warnings,
    )
