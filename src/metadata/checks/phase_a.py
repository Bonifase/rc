"""
Phase A -- no API calls, zero cost. Checks 1 (repurposed), 2, 3, 4, 5, 13,
14 (rule-based half only -- see check_14's docstring for why).

Every function has the same signature per the spec, even where a given
check doesn't need every argument -- this is what lets each check run and
be measured in isolation (the acceptance criteria's requirement).
"""
import re
from collections import defaultdict

from src.metadata.document import MetadataDoc
from src.metadata.extraction import DeclaredReference, expand_citation_markers
from src.metadata.findings import Finding

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}

_BRACKET_GROUP = re.compile(r"\[([\d,\s\u2013\u2014-]+)\]")
_DECLARED_COUNT_PHRASE = re.compile(
    r"\b(\d+)\s+(studies|references|articles|papers|trials|reports)\b", re.IGNORECASE
)
_PERCENTAGE = re.compile(r"\b(\d+(?:\.\d+)?)\s?%")

# A number (digit or word) immediately before a bracket, optionally through
# one intervening count-noun ("five studies [1-4]") -- anchored to the end
# of the preceding-text window, and the intervening word (when present) must
# itself be a plural count-noun, not just any word. Without that
# restriction, "The 2020 study [1,2]" would misread the year 2020 as a
# stated count of 2, since "study" would satisfy a generic \w+ just as well
# as "studies" would.
_COUNT_NOUNS = r"studies|papers|articles|trials|reports|reviews|sources|references"
_COUNT_BEFORE_BRACKET = re.compile(
    r"\b(\d+|" + "|".join(_NUMBER_WORDS.keys()) + r")\b(?:\s+(?:" + _COUNT_NOUNS + r"))?\s*$"
)


def check_01(doc: MetadataDoc, references: list[DeclaredReference], crossref_records: dict) -> list[Finding]:
    """
    REPURPOSED for this project's numbered+DOI input format.

    Original check 1 catches author-year citation keys (e.g. two different
    "Smith 2019"s) resolving ambiguously without an a/b suffix -- that
    specific ambiguity can't occur here, since every reference is a distinct
    number with its own DOI; two different papers simply get two different
    numbers.

    The analogous real failure mode in THIS format is the numbered-citation
    equivalent: two different reference NUMBERS pointing at the same
    resolved DOI -- a real data-entry bug (e.g. a paper accidentally listed
    twice under two numbers, or a copy-paste error in the bibliography),
    just the numbered-format version of the same underlying "which
    reference does this actually point to" ambiguity the original check
    protects against. Zero API calls: this only needs the declared DOIs,
    not a Crossref lookup.
    """
    findings = []
    doi_to_ref_ids: dict[str, list[int]] = defaultdict(list)
    for ref in references:
        if ref.doi:
            doi_to_ref_ids[ref.doi.lower()].append(ref.reference_id)

    for doi, ref_ids in doi_to_ref_ids.items():
        if len(ref_ids) > 1:
            for ref_id in ref_ids:
                findings.append(Finding(
                    check_id=1,
                    severity="critical",
                    reference_id=ref_id,
                    field="doi",
                    declared=doi,
                    actual=None,
                    message=(
                        f"Reference [{ref_id}] shares DOI {doi} with reference(s) "
                        f"{[r for r in ref_ids if r != ref_id]} -- the same paper appears "
                        "under more than one reference number."
                    ),
                ))
    return findings


def check_02(doc: MetadataDoc, references: list[DeclaredReference], crossref_records: dict) -> list[Finding]:
    """
    Partition coherence: if a sentence states a count next to a citation
    group ("all five studies [1-4]"), the stated count and the number of
    references actually in that bracket group should match.

    SCOPE NOTE: full "ranges and totals must add up" partition-coherence
    checking (arbitrary prose describing how N studies split into subsets
    that should sum back to N) is an open-ended language-understanding
    problem without an LLM, which contradicts this whole module's
    zero-token design. This implementation covers the well-defined,
    mechanically checkable subset: a number word or digit sitting
    immediately before a single bracket group, compared against that
    group's actual expanded reference count. It will not catch a count
    stated in a different sentence from its citation group, or a
    multi-clause partition ("20 of the 45 studies... the remaining 25...").
    """
    findings = []
    for sentence in doc.sentences:
        text = sentence["text"]
        for match in _BRACKET_GROUP.finditer(text):
            preceding = text[max(0, match.start() - 40): match.start()].lower()
            count_match = _COUNT_BEFORE_BRACKET.search(preceding)
            stated_count = None
            if count_match:
                token = count_match.group(1)
                stated_count = int(token) if token.isdigit() else _NUMBER_WORDS[token]

            if stated_count is None:
                continue

            group_text = f"[{match.group(1)}]"
            actual_count = len(expand_citation_markers(group_text))

            if stated_count != actual_count:
                findings.append(Finding(
                    check_id=2,
                    severity="warning",
                    field="citation_count",
                    declared=str(stated_count),
                    actual=str(actual_count),
                    message=(
                        f"Sentence '{sentence['id']}' states {stated_count} but the citation group "
                        f"{group_text} contains {actual_count} reference(s)."
                    ),
                ))
    return findings


def check_03(doc: MetadataDoc, references: list[DeclaredReference], crossref_records: dict) -> list[Finding]:
    """
    Declared reference count vs. references actually listed -- the
    simplest and cheapest check to run.

    SCOPE NOTE: relies on finding an unambiguous "N studies/references/
    articles/papers/trials/reports were [reviewed/included/analyzed]"-style
    phrase in the body text. If zero or more than one such phrase is found,
    this check abstains rather than guessing which one is authoritative --
    an abstention is safer than a false positive here.
    """
    matches = list(_DECLARED_COUNT_PHRASE.finditer(doc.body_text))
    if len(matches) != 1:
        return []

    declared_count = int(matches[0].group(1))
    actual_count = len(references)

    if declared_count != actual_count:
        return [Finding(
            check_id=3,
            severity="warning",
            field="reference_count",
            declared=str(declared_count),
            actual=str(actual_count),
            message=(
                f"The text states \"{matches[0].group(0)}\" but {actual_count} "
                "reference(s) are actually listed in the bibliography."
            ),
        )]
    return []


def check_04(doc: MetadataDoc, references: list[DeclaredReference], crossref_records: dict) -> list[Finding]:
    """
    Same figure/number cited differently across sections -- internal
    consistency only, no source comparison.

    SCOPE NOTE: "the same figure" in full generality requires knowing two
    different numeric mentions describe the same underlying quantity, which
    is a language-understanding problem. This implementation covers the
    well-defined, high-signal subset: the SAME reference number cited more
    than once in the document, with DIFFERENT percentage values quoted
    nearby each time -- a strong, mechanically detectable signal of exactly
    this problem ("ref [6] reported 25% in section 2 but 30% in section 5").
    It will not catch inconsistent non-percentage figures (counts, ratios,
    absolute values) or a number restated without a nearby citation marker.
    """
    ref_to_percentages: dict[int, list[tuple[str, str]]] = defaultdict(list)  # ref_id -> [(sentence_id, value), ...]

    for sentence in doc.sentences:
        text = sentence["text"]
        cited_refs = expand_citation_markers(text)
        if not cited_refs:
            continue
        for pct_match in _PERCENTAGE.finditer(text):
            for ref_id in cited_refs:
                ref_to_percentages[ref_id].append((sentence["id"], pct_match.group(1)))

    findings = []
    for ref_id, occurrences in ref_to_percentages.items():
        distinct_values = {value for _, value in occurrences}
        if len(distinct_values) > 1:
            first_sentence, first_value = occurrences[0]
            for sentence_id, value in occurrences[1:]:
                if value != first_value:
                    findings.append(Finding(
                        check_id=4,
                        severity="warning",
                        reference_id=ref_id,
                        field="value",
                        declared=f"{first_value}% (sentence {first_sentence})",
                        actual=f"{value}% (sentence {sentence_id})",
                        message=(
                            f"Reference [{ref_id}] is cited with {first_value}% in sentence "
                            f"{first_sentence} but {value}% in sentence {sentence_id}."
                        ),
                    ))
    return findings


def check_05(doc: MetadataDoc, references: list[DeclaredReference], crossref_records: dict) -> list[Finding]:
    """
    Year-volume coherence -- zero-API, internal-only version. Together with
    checks 10 and 11 (which compare against the real Crossref record) this
    forms the full field-by-field check; this one only checks that, among
    the DECLARED references citing the same journal, year and volume move
    together (a later year shouldn't have a strictly lower volume than an
    earlier year for the same journal) -- catchable from the bibliography
    alone, no external data needed.
    """
    by_journal: dict[str, list[DeclaredReference]] = defaultdict(list)
    for ref in references:
        if ref.journal and ref.year is not None and ref.volume and ref.volume.isdigit():
            by_journal[ref.journal.strip().lower()].append(ref)

    findings = []
    for journal, refs in by_journal.items():
        if len(refs) < 2:
            continue
        refs_sorted = sorted(refs, key=lambda r: r.year)
        for earlier, later in zip(refs_sorted, refs_sorted[1:]):
            if later.year > earlier.year and int(later.volume) < int(earlier.volume):
                findings.append(Finding(
                    check_id=5,
                    severity="warning",
                    reference_id=later.reference_id,
                    field="volume",
                    declared=later.volume,
                    actual=None,
                    message=(
                        f"Reference [{later.reference_id}] ({journal}, {later.year}, vol. {later.volume}) "
                        f"has a lower volume than reference [{earlier.reference_id}] from an earlier year "
                        f"({earlier.year}, vol. {earlier.volume}) in the same journal."
                    ),
                ))
    return findings


def check_13(doc: MetadataDoc, references: list[DeclaredReference], crossref_records: dict) -> list[Finding]:
    """
    Citation markers vs. bibliography, both directions:
    - orphaned markers: cited in the text but not in the bibliography
    - never-cited entries: listed in the bibliography but never cited
    """
    declared_numbers = {ref.reference_id for ref in references}
    cited_numbers: set[int] = set()
    orphan_findings = []

    for sentence in doc.sentences:
        for ref_num in expand_citation_markers(sentence["text"]):
            cited_numbers.add(ref_num)
            if ref_num not in declared_numbers:
                orphan_findings.append(Finding(
                    check_id=13,
                    severity="critical",
                    field="citation_marker",
                    declared=str(ref_num),
                    actual=None,
                    message=(
                        f"Sentence '{sentence['id']}' cites [{ref_num}], which does not appear "
                        "in the reference list."
                    ),
                ))

    never_cited_findings = [
        Finding(
            check_id=13,
            severity="warning",
            reference_id=ref.reference_id,
            field="citation_marker",
            declared=None,
            actual=None,
            message=f"Reference [{ref.reference_id}] is listed in the bibliography but never cited in the text.",
        )
        for ref in references
        if ref.reference_id not in cited_numbers
    ]

    return orphan_findings + never_cited_findings


def check_14(doc: MetadataDoc, references: list[DeclaredReference], crossref_records: dict) -> list[Finding]:
    """
    Rule-based half only. Per the spec: "Declared screening criterion +
    authors omitted without 'et al.'" bundles a rule-based sub-check with a
    genuinely semantic one.

    DEVIATION FROM THE SPEC'S EXECUTION-ORDER TABLE, DOCUMENTED HERE:
    the table lists check 14 under Phase A (zero API). But meaningfully
    detecting "authors were silently dropped from the declared list WITHOUT
    an et al./ellipsis marker" requires knowing the REAL author count --
    that only exists after Crossref resolution (Phase B/C), which
    contradicts "zero API." Rather than force a fake zero-API version of
    that comparison, this function implements only the part that's
    genuinely checkable from the declared text alone: a truncation marker
    ("...", "et al.") with NO named authors before it at all is malformed
    on its face, regardless of what the real author list turns out to be.

    The substantive version -- comparing the declared author list's length
    against the real Crossref author count when no truncation marker is
    present -- is implemented in checks/phase_c.py's check_08_author_checks,
    colocated with check 8 since both need the same resolved author data
    and both only become available once Phase B/C's resolution has run.

    The screening-criterion half (semantic, needs a model call) is not
    implemented here at all -- see phase_c.py for its explicit
    not_evaluated placeholder finding, so its absence is visible in the
    report rather than silently missing.
    """
    findings = []
    for ref in references:
        if ref.authors_truncated and len(ref.authors) == 0:
            findings.append(Finding(
                check_id=14,
                severity="warning",
                reference_id=ref.reference_id,
                field="authors",
                declared=ref.raw,
                actual=None,
                message=(
                    f"Reference [{ref.reference_id}] uses a truncation marker ('...' or 'et al.') "
                    "but no author is named before it."
                ),
            ))
    return findings
