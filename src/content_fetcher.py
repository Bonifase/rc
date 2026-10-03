"""
Step 3: Fetch source content -- full text if a PMCID exists, abstract if only
a PMID exists, nothing if neither.

Distinct outcomes (must NOT be collapsed):
- full_text:        got body text from PMC (JATS XML), the strongest signal
- abstract_only:     only got a PubMed abstract, either because there's no
                       PMCID, or because there IS a PMCID but its full-text
                       XML isn't retrievable (open-access status dependent --
                       this is the gap the guide explicitly flags as unsolved
                       and worth reporting back on)
- unavailable:       neither PMID nor PMCID was provided -- nothing to fetch,
                       not an error
- error:             transient failure (network, 5xx, malformed response)

Two NCBI E-utilities are used:
- EFetch on PMC (db=pmc) for full-text JATS XML
- EFetch/ESummary on PubMed (db=pubmed) for abstracts
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import (
    NCBI_EUTILS_BASE_URL,
    REQUEST_TIMEOUT_SECONDS,
    MAX_RETRIES,
    require_contact_email,
    NCBI_API_KEY,
)

# ---------------------------------------------------------------------------
# Text normalizer
# ---------------------------------------------------------------------------
# Reference markers, e.g. [42] [1,3,5]
_RE_REFS = re.compile(r"\[[\d,\s\-–]+\]")
# Em-dash / en-dash / figure dash / Unicode minus → hyphen
_RE_DASH = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
# Plus-minus sign → +/-
_RE_PLUSMINUS = re.compile(r"\u00b1")
# Multiplication sign → x
_RE_TIMES = re.compile(r"\u00d7")
# Middle dot, bullets, non-breaking space, en/em/ideographic/zero-width
# space variants (U+2000–U+200A, U+202F USM, U+3000 ideographic, U+2060 word
# joiner) → space. Thin space U+2009 is what NCBI/publishers use to separate
# numbers from units (e.g. "120 µg/mL").
_RE_SPECIAL = re.compile(
    r"[\u00b7\u2022\u2027\u205f\u00a0\u1680\u2000-\u200a\u202f\u3000\u2060]"
)
# Zero-width spaces / BOM / zero-width no-break space → removed entirely,
# so they can't glue words together (e.g. "mU+200Bg").
_RE_ZWS = re.compile(r"[\u200b\ufeff]")
# Superscript/subscript digits/operators, micro sign (U+00B5) and Greek mu
# (U+03BC) → ASCII, so mL²/µg/x10⁵ match between gold quotes and fetched XML.
_SUPER_SUB = str.maketrans(
    {
        "\u00b2": "2", "\u00b3": "3", "\u00b9": "1",
        "\u2070": "0", "\u2074": "4", "\u2075": "5", "\u2076": "6",
        "\u2077": "7", "\u2078": "8", "\u2079": "9",
        "\u207a": "+", "\u207b": "-",
        "\u2080": "0", "\u2081": "1", "\u2082": "2", "\u2083": "3",
        "\u2084": "4", "\u2085": "5", "\u2086": "6", "\u2087": "7",
        "\u2088": "8", "\u2089": "9", "\u208a": "+", "\u208b": "-",
        "\u00b5": "u", "\u03bc": "u",
    }
)
# Collapse whitespace
_RE_WS = re.compile(r"\s+")

_CURLY_TO_ASCII = str.maketrans(
    {"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
     "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"'}
)


def _normalize_for_match(text: str) -> str:
    """
    Strip reference markers, unify punctuation to ASCII (dashes, curly
    quotes, plush/minus, multiplication, Unicode spaces, super/subscript
    digits, micro sign), lowercase, collapse whitespace.  This is for
    *matching only* -- the raw text returned by fetch_content is left
    untouched.
    """
    t = _RE_REFS.sub("", text)
    t = _RE_DASH.sub("-", t)
    t = _RE_PLUSMINUS.sub("+/-", t)
    t = _RE_TIMES.sub("x", t)
    t = _RE_SPECIAL.sub(" ", t)
    t = _RE_ZWS.sub("", t)
    t = t.translate(_SUPER_SUB)
    t = t.translate(_CURLY_TO_ASCII)
    t = t.lower()
    t = _RE_WS.sub(" ", t).strip()
    return t


def _sentences(text: str) -> list[str]:
    """
    Split fetched text into sentence-like chunks.  Uses newline and period
    as rough delimiters -- good enough for matching, no NLP dependency.
    """
    chunks: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Skip section headings and sentence IDs
        if line.startswith("##") or line.startswith("S0"):
            continue
        # Split on period followed by space or end-of-string, keep periods
        for seg in re.split(r"(?<=[.!?])\s+", line):
            seg = seg.strip()
            if seg:
                chunks.append(seg)
    return chunks


def gold_sentence_present(
    gold_quote: str,
    fetched_text: str,
    threshold: float = 0.90,
) -> tuple[bool, float, str]:
    """
    Determine whether a gold evidence sentence is present in fetched text
    using sequence similarity (not content-word overlap).

    Checks both full-text match and per-sentence match, takes the best.

    Returns (present, best_ratio, best_sentence).
    - present: True if best_ratio >= threshold (default 0.90)
    - best_ratio: SequenceMatcher ratio of best match (0.0-1.0)
    - best_sentence: the fetched sentence (or full text) that scored highest
    """
    gold_norm = _normalize_for_match(gold_quote)

    best_ratio = 0.0
    best_sentence = ""

    # Full-text check (handles gold spanning multiple sentences)
    full_norm = _normalize_for_match(fetched_text)
    full_ratio = SequenceMatcher(None, gold_norm, full_norm).ratio()
    if full_ratio > best_ratio:
        best_ratio = full_ratio
        best_sentence = "(full text)"

    # Per-sentence check (handles gold matching one sentence exactly)
    for s in _sentences(fetched_text):
        s_norm = _normalize_for_match(s)
        ratio = SequenceMatcher(None, gold_norm, s_norm).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_sentence = s

    # Substring check (gold is a contiguous substring of fetched, after norm)
    if gold_norm in full_norm:
        best_ratio = 1.0
        best_sentence = "(substring match)"
    else:
        # Robustness for a gold quote that the fetched text carries as several
        # separate sentences (e.g. a blockquote the publisher split apart with
        # section headings between the parts): the quote is "present" if every
        # gold sentence appears verbatim (normalized substring) somewhere.
        gold_sents = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[(])", gold_quote)
            if len(s.strip()) >= 12
        ]
        gold_sents_norm = [_normalize_for_match(s) for s in gold_sents]
        if (
            len(gold_sents) >= 2
            and all(g in full_norm for g in gold_sents_norm)
        ):
            best_ratio = 1.0
            best_sentence = f"(per-sentence, {len(gold_sents)} parts)"

    # Format-tolerant substring: present verbatim ignoring whitespace and
    # punctuation. Catches inline element-boundary artifacts that keep the
    # ordinary substring from matching while the words are truly all there
    # (e.g. "...identified -. in addition..." vs gold "...identified. in
    # addition..."). This is NOT paraphrase matching -- it still requires the
    # exact word sequence.
    if best_ratio < 1.0 and len(gold_norm) >= 12:
        collapsed_gold = re.sub(r"[^a-z0-9]", "", gold_norm)
        collapsed_full = re.sub(r"[^a-z0-9]", "", full_norm)
        if collapsed_gold and collapsed_gold in collapsed_full:
            best_ratio = 1.0
            best_sentence = "(collapsed substring match)"

    return best_ratio >= threshold, best_ratio, best_sentence


class NcbiFetchTransientError(Exception):
    """Raised for retryable failures (timeouts, 5xx, connection errors)."""


@dataclass
class ContentResult:
    status: str  # "full_text" | "abstract_only" | "unavailable" | "abstained" | "error"
    source: Optional[str] = None  # "pmc" | "pubmed" | "openalex" | "unpaywall" | ...
    text: Optional[str] = None
    error_message: Optional[str] = None
    # --- extended retrieval structured fields (Eduardo's directive) ---
    abstract_text: Optional[str] = None
    body_text: Optional[str] = None
    retrieval_source: Optional[str] = None
    retrieval_status: Optional[str] = None  # "full" | "abstract_only" | "abstained" | ...
    structure_status: Optional[str] = None  # "resolved" | "unresolved" | "not_applicable"
    final_url: Optional[str] = None
    content_hash: Optional[str] = None


def _ncbi_params(extra: dict, contact_email: str) -> dict:
    params = {
        "tool": "citation-fidelity-checker",
        "email": contact_email,
        **extra,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    return params


@retry(
    reraise=True,
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(NcbiFetchTransientError),
)
def _efetch(db: str, id_: str, contact_email: str, rettype: str = None) -> requests.Response:
    params = {"db": db, "id": id_, "retmode": "xml"}
    if rettype:
        params["rettype"] = rettype

    try:
        response = requests.get(
            f"{NCBI_EUTILS_BASE_URL}/efetch.fcgi",
            params=_ncbi_params(params, contact_email),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        # Network-level failures must go through the same retry path as a
        # 5xx -- raising the bare requests exception here bypasses tenacity,
        # since retry_if_exception_type only matches NcbiFetchTransientError.
        raise NcbiFetchTransientError(f"Network error calling NCBI EFetch ({db}): {exc}") from exc
    if response.status_code >= 500 or response.status_code == 429:
        raise NcbiFetchTransientError(f"NCBI EFetch ({db}) returned {response.status_code}")
    response.raise_for_status()
    return response


def _elem_text(el) -> str:
    """Flatten all text under an element, collapsing whitespace."""
    return " ".join("".join(el.itertext()).split())


def _extract_table_block(table_wrap) -> Optional[str]:
    """
    Render a <table-wrap> as a labeled block: its label/caption (e.g.
    "Table 2. Rediploidization ages by region") followed by one line per
    row, cells joined with " | ". This is the data the excerpt was
    previously missing entirely -- <table-wrap> content lives outside any
    <p> tag, so the old <p>-only walk never saw it, even though this is
    exactly where a paper's headline numbers often live.
    """
    label = _elem_text(table_wrap.find("label")) if table_wrap.find("label") is not None else ""
    caption = _elem_text(table_wrap.find("caption")) if table_wrap.find("caption") is not None else ""
    header = " ".join(x for x in (label, caption) if x)

    rows: list[str] = []
    table_el = table_wrap.find(".//table")
    if table_el is not None:
        for tr in table_el.iter("tr"):
            cells = [_elem_text(c) for c in tr if c.tag in ("td", "th")]
            cells = [c for c in cells if c]
            if cells:
                rows.append(" | ".join(cells))

    lines: list[str] = []
    if header:
        lines.append(f"[Table] {header}")
    lines.extend(rows)

    # Table footnotes (abbreviation definitions, methods notes) live in
    # <table-wrap-foot>, OUTSIDE the <table> element -- a <p>-only walk would
    # never see them. They are real content (e.g. "mafbx, muscle atrophy
    # F-box") and must not be silently dropped.
    foot = table_wrap.find("table-wrap-foot")
    if foot is not None:
        foot_texts = [_elem_text(p_el) for p_el in foot.iter("p")]
        foot_texts = [t for t in foot_texts if t]
        if not foot_texts:
            ft = _elem_text(foot)
            if ft:
                foot_texts.append(ft)
        for ft in foot_texts:
            lines.append(f"[Footnote] {ft}")
    return "\n".join(lines) if lines else None


def _extract_figure_block(fig) -> Optional[str]:
    """
    Render a <fig> as a labeled caption block (e.g. "[Figure] Fig. 3.
    Boxplots of ..."). Figure captions frequently state the finding a
    figure supports in words, even though the figure image itself can't
    be extracted -- another source that was silently dropped before.
    """
    label = _elem_text(fig.find("label")) if fig.find("label") is not None else ""
    caption = _elem_text(fig.find("caption")) if fig.find("caption") is not None else ""
    text = " ".join(x for x in (label, caption) if x)
    return f"[Figure] {text}" if text else None


_REF_ROOTS = {"ref-list", "ref"}
_NONPROSE_CONTAINERS = ("title", "label")


def _supplementary_block(el) -> Optional[str]:
    """Render a <supplementary-material> as a one-line block: any <label>
    (e.g. "Supplementary Material"), caption <title> (e.g. the file/table
    label "Supplemental_Table_S5.xlsx" or "Table S3. ..."), and file
    <object-id>. The attachments themselves are files, not HTML content,
    but their labels are what the browser shows and must be kept."""
    label = el.find("label")
    cap = el.find("caption")
    cap_title = cap.find("title") if cap is not None else None
    oid = el.find(".//object-id")
    parts = [_elem_text(x) for x in (label, cap_title, oid) if x is not None]
    parts = [p for p in parts if p]
    # de-duplicate adjacent repeats (label + caption title often duplicate)
    seen: list[str] = []
    for p in parts:
        if not seen or p not in seen:
            seen.append(p)
    if seen:
        return "[Supplementary] " + " ".join(seen)
    return None


def _def_item_block(el) -> Optional[str]:
    """Render a <def-item> as "term: definition" so glossary/abbreviation
    pairs keep their meaning instead of being flattened into loose text."""
    term = el.find("term")
    defn = el.find("def")
    tt = _elem_text(term) if term is not None else ""
    dd = _elem_text(defn) if defn is not None else ""
    if tt and dd:
        return f"{tt}: {dd}"
    if tt or dd:
        return tt or dd
    return None


def _boxed_block(el) -> Optional[str]:
    """Heading for a <boxed-text>: "[Box] {label} {caption title}" when
    present (e.g. "Box 1 Key computational tasks")."""
    label = el.find("label")
    cap = el.find("caption")
    cap_title = cap.find("title") if cap is not None else None
    parts = [p for p in (_elem_text(label) if label is not None else None,
                         _elem_text(cap_title) if cap_title is not None else None) if p]
    if parts:
        return "[Box] " + " ".join(parts)
    return None


def _walk_body_children(el, blocks: list[str], _skip_own_title=None) -> None:
    """Dispatch the direct children of `el` (used by _walk_body; also re-used
    to walk the *contents* of a <boxed-text>, whose own caption title must be
    suppressed)."""
    for child in el:
        if _skip_own_title is not None and child is _skip_own_title:
            continue
        tag = child.tag
        if tag in ("sec", "app"):
            title_el = child.find("title")
            if title_el is not None:
                title_text = _elem_text(title_el)
                if title_text:
                    blocks.append(f"## {title_text}")
            _walk_body(child, blocks, _skip_own_title=title_el)
        elif tag == "p":
            _walk_paragraph(child, blocks)
        elif tag == "table-wrap":
            block = _extract_table_block(child)
            if block:
                blocks.append(block)
        elif tag == "fig":
            block = _extract_figure_block(child)
            if block:
                blocks.append(block)
        elif tag == "title":
            title_text = _elem_text(child)
            if title_text:
                blocks.append(f"## {title_text}")
        else:
            _walk_body(child, blocks)


def _walk_body(el, blocks: list[str], _skip_own_title=None) -> None:
    """
    Walk the body tree in document order, emitting one block per element:
    - <sec>/<app>: its <title> becomes a "## Heading" marker, then its
      children are walked normally (its own <title> element is skipped in
      the recursion to avoid double-emitting).
    - <p>: plain paragraph text. If a <p> MIXES prose with <fig>/<table-wrap>
      /<boxed-text> children inline (common in Taylor & Francis XML), the
      floats are rendered as proper [Figure]/[Table]/[Box] blocks instead of
      everyone being flattened together.
    - <table-wrap> / <fig>: handled via the helpers above and NOT recursed
      into further, so their inner <p> tags (caption/footnote text) aren't
      also picked up a second time as bare paragraphs.
    - <boxed-text>: "[Box] label + caption title" heading, then walked (its
      caption title is skipped to avoid duplication). This keeps "Box 1 Key
      computational tasks / Co-registration of modalities ..." as real
      structure instead of one run-on paragraph.
    - <supplementary-material>: single "[Supplementary] ..." line (label /
      caption title / file id). Attachments are files, not HTML content.
    - <def-item>: "term: definition" line (glossary/abbreviation pairs).
    - <title> in any other container (fn-group, notes, ack, glossary, ...):
      becomes a "## Heading" marker -- otherwise back-matter headings like
      "Institutional Review Board Statement" were silently dropped while
      their content was kept.
    - anything else: recurse to find any nested element handled above.
    References (<ref-list>/<ref>) are pruned entirely -- bibliography entries
    are intentionally not part of the fetched text.
    """
    if el is None:
        return
    if el.tag in _REF_ROOTS:
        return
    if el.tag == "boxed-text":
        head = _boxed_block(el)
        if head:
            blocks.append(head)
        cap = el.find("caption")
        cap_title = cap.find("title") if cap is not None else None
        _walk_body_children(el, blocks, _skip_own_title=cap_title)
        return
    if el.tag == "supplementary-material":
        block = _supplementary_block(el)
        if block:
            blocks.append(block)
        else:
            _walk_body_children(el, blocks)
        return
    if el.tag == "def-item":
        block = _def_item_block(el)
        if block:
            blocks.append(block)
        return

    _walk_body_children(el, blocks, _skip_own_title=_skip_own_title)


def _walk_paragraph(p_el, blocks: list[str]) -> None:
    """
    Render a <p> that may itself contain inline <fig>/<table-wrap>/<boxed-text>
    children (publisher-dependent mixing). If there are none, emit the paragraph
    as a single block. If there are some, emit prose, figure, prose, table, ... in
    document order so the [Figure]/[Table]/[Box] markers survive instead of being
    flattened away by _elem_text.
    """
    floats = [c for c in p_el if c.tag in ("fig", "table-wrap", "boxed-text")]
    if not floats:
        text = _elem_text(p_el)
        if text:
            blocks.append(text)
        return

    # Builder that accumulates inline text until a float interrupts it.
    # ElementTree: p_el.text holds prose before the first child, each child's
    # .tail holds prose after it -- both must be preserved.
    def emit(buf: list[str]) -> None:
        text = " ".join(" ".join(buf).split()).strip()
        if text:
            blocks.append(text)

    acc: list[str] = []
    if p_el.text:
        acc.append(p_el.text)
    for child in p_el:
        if child.tag == "fig":
            block = _extract_figure_block(child)
            if block:
                emit(acc)
                acc = []
                blocks.append(block)
        elif child.tag == "table-wrap":
            block = _extract_table_block(child)
            if block:
                emit(acc)
                acc = []
                blocks.append(block)
        elif child.tag == "boxed-text":
            emit(acc)
            acc = []
            _walk_body(child, blocks)
        else:
            acc.append(" ".join("".join(child.itertext()).split()))
        if child.tail:
            acc.append(child.tail)
    emit(acc)


def _extract_pmc_body_text(xml_bytes: bytes) -> Optional[str]:
    """
    Pull body text out of a JATS XML article. Returns None if there's no
    <body> element -- which happens even for records that HAVE a PMCID, if
    the publisher hasn't made full text available via EFetch (open-access
    status dependent). This is the exact gap the guide flags as unsolved.

    Unlike the original <p>-only extraction, this also preserves section
    headings and pulls in table and figure caption content, so numbers
    that live in a table cell or figure caption (rather than body prose)
    are no longer silently dropped from the excerpt sent to the model.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    # A record with only front matter (title/abstract) is NOT full text: it
    # must fall through to the PubMed abstract so the status taxonomy keeps
    # distinguishing full_text from abstract_only. The title/abstract/
    # keyword enrichment below only applies to real full-text records.
    if root.find(".//body") is None:
        return None

    blocks: list[str] = []

    # Article title -- a claim can be cited straight from the title.
    article_meta = root.find(".//front/article-meta")
    if article_meta is not None:
        title_el = article_meta.find("title-group/article-title")
        if title_el is not None:
            title_text = _elem_text(title_el)
            if title_text:
                blocks.append("## Article Title")
                blocks.append(title_text)

    # Abstracts -- there may be several (e.g. "Simple Summary" plus "Abstract"),
    # and each may carry its own <sec>/<title>. Preserve that structure so the
    # model can tell a plain-language summary from the scientific abstract.
    if article_meta is not None:
        for abstract_el in article_meta.findall("abstract"):
            _walk_abstract(abstract_el, blocks)

    # Keywords -- often a claim's searchable vocabulary lives here.
    if article_meta is not None:
        kwd_texts = []
        for kwd in article_meta.iter("kwd"):
            t = _elem_text(kwd)
            if t:
                kwd_texts.append(t)
        if kwd_texts:
            blocks.append("## Keywords")
            blocks.append("; ".join(kwd_texts))

    body = root.find(".//body")
    if body is not None:
        _walk_body(body, blocks)

    # Float content that lives OUTSIDE <body> (floats-group/floats-wrap) and
    # the back matter (appendices, acknowledgments, footnotes, glossaries) --
    # both are skipped by a naive <body>-only walk. A cited claim can live in
    # an appendix or a float (e.g. Supplementary/Extended Data figure caption),
    # so these are real content, not boilerplate to drop.
    for floats_el in root.findall(".//floats-group") + root.findall(".//floats-wrap"):
        _walk_body(floats_el, blocks)
    for back_el in root.findall(".//back"):
        _walk_body(back_el, blocks)

    text = "\n\n".join(blocks)
    return text if text.strip() else None


def _walk_abstract(abstract_el, blocks: list[str]) -> None:
    """
    Walk an <abstract> element recursively, in document order, without any
    case-specific handling:
    - every <title> anywhere in the tree (a <sec> heading like "Simple
      Summary" / "Abstract", a bare abstract-level <title>, or a nested
      sub-section <title>) becomes a "## Title" marker
    - every <p> stays plain text, with inline <fig>/<table-wrap> rendered
      as proper blocks by _walk_paragraph
    - <table-wrap>/<fig>/<sec> nest arbitrarily and are walked into, so no
      substantive title or paragraph is skipped no matter how deep it sits
    Falls back to flat text only if the abstract has no structural content.
    """
    def _walk(el) -> bool:
        produced = False
        for child in el:
            if child.tag == "title":
                title_text = _elem_text(child)
                if title_text:
                    blocks.append(f"## {title_text}")
                    produced = True
            elif child.tag == "p":
                before = len(blocks)
                _walk_paragraph(child, blocks)
                produced = produced or len(blocks) > before
            elif child.tag == "table-wrap":
                block = _extract_table_block(child)
                if block:
                    blocks.append(block)
                    produced = True
            elif child.tag == "fig":
                block = _extract_figure_block(child)
                if block:
                    blocks.append(block)
                    produced = True
            else:
                produced = _walk(child) or produced
        return produced

    if not _walk(abstract_el):
        text = _elem_text(abstract_el)
        if text:
            blocks.append(text)


def _extract_pubmed_abstract(xml_bytes: bytes) -> Optional[str]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    abstract_texts = [
        "".join(el.itertext()).strip()
        for el in root.iter("AbstractText")
        if "".join(el.itertext()).strip()
    ]
    if not abstract_texts:
        return None
    return "\n\n".join(abstract_texts)


_ABSTRACT_PLACEHOLDERS = {
    "no abstract available",
    "abstract not available",
    "not available",
    "no abstract",
    "n/a",
    "none",
    "abstract unavailable",
}


def is_usable_abstract(text: Optional[str], min_length: int = 40) -> bool:
    """
    Guard 3: existence of the abstract field is not enough. Crossref/PubMed
    sometimes return a near-empty string, a bare placeholder line, or a
    handful of words -- none of which is real content to judge a claim
    against. Reject those before they ever reach the LLM.
    """
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < min_length:
        return False
    if stripped.lower() in _ABSTRACT_PLACEHOLDERS:
        return False
    return True


def fetch_content(
    pmid: Optional[str],
    pmcid: Optional[str],
    contact_email: Optional[str] = None,
) -> ContentResult:
    """
    Try full text via PMC first (if pmcid given), fall back to abstract via
    PubMed (if pmid given), otherwise report unavailable. Never raises on
    "no full text found" -- that's a valid, expected outcome to flag, not
    a bug to crash on.
    """
    email = contact_email or require_contact_email()

    if not pmid and not pmcid:
        return ContentResult(status="unavailable")

    if pmcid:
        try:
            # PMC IDs are commonly passed with or without the "PMC" prefix
            # depending on source; EFetch wants the bare numeric ID.
            bare_id = pmcid.replace("PMC", "").replace("pmc", "")
            response = _efetch("pmc", bare_id, email)
        except (NcbiFetchTransientError, requests.RequestException) as exc:
            return ContentResult(status="error", error_message=str(exc))

        body_text = _extract_pmc_body_text(response.content)
        if body_text:
            return ContentResult(status="full_text", source="pmc", text=body_text)
        # PMCID exists but full text wasn't retrievable -- fall through to abstract.

    if pmid:
        try:
            response = _efetch("pubmed", pmid, email, rettype="abstract")
        except (NcbiFetchTransientError, requests.RequestException) as exc:
            return ContentResult(status="error", error_message=str(exc))

        abstract = _extract_pubmed_abstract(response.content)
        if abstract:
            return ContentResult(status="abstract_only", source="pubmed", text=abstract)

    return ContentResult(status="unavailable")


def fetch_source_content(
    doi: Optional[str] = None,
    pmid: Optional[str] = None,
    pmcid: Optional[str] = None,
    title: Optional[str] = None,
    first_author: Optional[str] = None,
    publication_year: Optional[int] = None,
    journal: Optional[str] = None,
    contact_email: Optional[str] = None,
    seed_url: Optional[str] = None,
    cache_key: Optional[str] = None,
    cache_dir: Optional[str] = None,
) -> ContentResult:
    """
    Extended retrieval entry point (Eduardo's "extended retrival" directive).

    Keeps the fast NCBI path as the head of the cascade, then falls back
    through Europe PMC / Unpaywall / OpenAlex / Semantic Scholar / open
    publisher pages / institutional repositories when the classic chain
    leaves content unreachable. Returns the structured fields in every
    case: abstract_text, body_text, retrieval_source, retrieval_status,
    structure_status, final_url, content_hash.

    Identifiers that the caller asserts (pmcid/pmid) are themselves the
    identity proof; any *retrieved* source is independently checked (exact
    DOI, or normalized title + first author + year, checked against the
    document content). Ambiguous matches are returned as status="abstained"
    instead of being auto-matched.
    """
    from src.retrieval import Citation, retrieve_extended

    email = contact_email or require_contact_email()
    cit = Citation(
        title=title or None,
        first_author=first_author or None,
        publication_year=publication_year,
        doi=doi or None,
        pmid=pmid or None,
        pmcid=pmcid or None,
        journal=journal or None,
    )
    res = retrieve_extended(cit, contact_email=email, seed_url=seed_url,
                            cache_key=cache_key, cache_dir=cache_dir)

    if res.status == "full_text":
        return ContentResult(
            status="full_text",
            source=res.retrieval_source,
            text=res.text,
            abstract_text=res.abstract_text,
            body_text=res.body_text,
            retrieval_source=res.retrieval_source,
            retrieval_status=res.retrieval_status,
            structure_status=res.structure_status,
            final_url=res.final_url,
            content_hash=res.content_hash,
            error_message=res.error_message,
        )
    if res.status == "abstract_only":
        return ContentResult(
            status="abstract_only",
            source=res.retrieval_source,
            text=res.abstract_text,
            abstract_text=res.abstract_text,
            retrieval_source=res.retrieval_source,
            retrieval_status=res.retrieval_status,
            structure_status=res.structure_status,
            final_url=res.final_url,
            content_hash=res.content_hash,
        )
    if res.status == "abstained":
        return ContentResult(
            status="abstained",
            error_message=res.abstained_reason,
            retrieval_status="abstained",
            content_hash=res.content_hash,
            final_url=res.final_url,
        )
    if res.status == "not_found":
        return ContentResult(
            status="unavailable",
            error_message=res.identity_note or "no source reachable",
        )
    return ContentResult(
        status="error",
        error_message=res.error_message or "extended retrieval failed",
    )