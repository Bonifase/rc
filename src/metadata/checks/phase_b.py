"""
Phase B -- checks 7, 9, 10, 11, 15, 16. Per the execution order, these run
right after resolution (1 API call per reference, already spent resolving it)
and add no further cost EXCEPT check 11, which needs one additional call per
distinct journal (ISSN) to sample its published volume range -- cached so a
journal cited by many references only gets sampled once per run. Check 15 is
free: retraction status ships in the SAME resolved record's JSON (Crossref's
free Retraction Watch integration, `updated-by`/`update-to`), so no extra call.

All of them operate on (declared, actual) pairs where resolution has already
determined the actual record is the right paper (status was
"_needs_field_comparison" in resolution.py) -- these checks decide whether
it's well_cited or miscited, and why.
"""
from difflib import SequenceMatcher
from typing import Optional

from src.metadata.document import MetadataDoc
from src.metadata.extraction import DeclaredReference
from src.metadata.crossref_metadata import ActualReference, title_similarity, fetch_journal_volume_samples
from src.metadata.findings import Finding

# Stricter than resolution's 0.85 gate -- resolution only needs "probably
# the right paper" to proceed; check 9 asks "was the title actually
# transcribed verbatim," a materially different, tighter bar.
VERBATIM_TITLE_THRESHOLD = 0.97

# Journal-name comparison tolerates minor formatting/punctuation
# differences but not a genuinely different journal.
JOURNAL_SIMILARITY_THRESHOLD = 0.80


def _normalize_journal(name: str) -> str:
    return " ".join(name.lower().replace("&", "and").split())


def check_07(
    doc: MetadataDoc,
    references: list[DeclaredReference],
    crossref_records: dict[int, ActualReference],
) -> list[Finding]:
    """
    "The quietest error: link works, leads to a real but different paper."

    resolution.py's own routing already filters out references whose title
    doesn't clear the 0.85 similarity bar (those become wrong_article or
    synthesized_title before ever reaching here). What slips through that
    gate is the genuinely quiet case: title matches well (a close
    paraphrase can score high), but the first author doesn't match at all
    -- a real signal that this might still be the wrong paper, or at least
    that something is off. This function is where that fuller check (title
    AND first-author) actually happens; resolution.py's gate is
    deliberately simpler (title-only) so the blocking/non-blocking routing
    decision doesn't get tangled up with authorship data quality.
    """
    findings = []
    for ref in references:
        actual = crossref_records.get(ref.reference_id)
        if actual is None or actual.status != "resolved" or not ref.title or not actual.title:
            continue

        score = title_similarity(ref.title, actual.title)
        if score < 0.85:
            continue  # not this check's concern -- resolution already handles a bad title match

        declared_first_author = ref.authors[0].family.lower() if ref.authors and ref.authors[0].family else None
        actual_family_names = {a.family.lower() for a in actual.authors if a.family}

        if declared_first_author and actual_family_names and declared_first_author not in actual_family_names:
            findings.append(Finding(
                check_id=7,
                severity="critical",
                reference_id=ref.reference_id,
                field="author",
                declared=declared_first_author,
                actual=", ".join(sorted(actual_family_names)) or None,
                message=(
                    f"Reference [{ref.reference_id}]: title matches the resolved record (similarity "
                    f"{score:.2f}), but the declared first author '{declared_first_author}' does not "
                    "appear in the resolved record's author list -- the identifier may point to a "
                    "different paper than declared."
                ),
            ))
    return findings


def check_09(
    doc: MetadataDoc,
    references: list[DeclaredReference],
    crossref_records: dict[int, ActualReference],
) -> list[Finding]:
    """
    Synthesized title: the paper exists (resolution already confirmed a
    good-enough match to proceed), but the declared title wasn't
    transcribed verbatim -- a looser paraphrase than a real citation should
    ever produce. Distinct from resolution's own synthesized_title status,
    which only fires on the search-based routes when NO confident match
    was found at all; this fires on references that DID resolve well but
    still show title drift.
    """
    findings = []
    for ref in references:
        actual = crossref_records.get(ref.reference_id)
        if actual is None or actual.status != "resolved" or not ref.title or not actual.title:
            continue

        score = title_similarity(ref.title, actual.title)
        if 0.85 <= score < VERBATIM_TITLE_THRESHOLD:
            findings.append(Finding(
                check_id=9,
                severity="warning",
                reference_id=ref.reference_id,
                field="title",
                declared=ref.title,
                actual=actual.title,
                message=(
                    f"Reference [{ref.reference_id}]'s declared title is a close but not verbatim "
                    f"match to the resolved record's title (similarity {score:.2f})."
                ),
            ))
    return findings


def check_10(
    doc: MetadataDoc,
    references: list[DeclaredReference],
    crossref_records: dict[int, ActualReference],
) -> list[Finding]:
    """
    Journal substitution: journal/volume/pages are internally consistent in
    the declared reference but don't match the real record. A field-diff
    per field, as the spec suggests.
    """
    findings = []
    for ref in references:
        actual = crossref_records.get(ref.reference_id)
        if actual is None or actual.status != "resolved":
            continue

        if ref.journal and actual.journal:
            similarity = SequenceMatcher(None, _normalize_journal(ref.journal), _normalize_journal(actual.journal)).ratio()
            if similarity < JOURNAL_SIMILARITY_THRESHOLD:
                findings.append(Finding(
                    check_id=10, severity="critical", reference_id=ref.reference_id,
                    field="journal", declared=ref.journal, actual=actual.journal,
                    message=f"Reference [{ref.reference_id}] declares journal '{ref.journal}' but the record shows '{actual.journal}'.",
                ))

        if ref.volume and actual.volume and ref.volume.strip() != str(actual.volume).strip():
            findings.append(Finding(
                check_id=10, severity="warning", reference_id=ref.reference_id,
                field="volume", declared=ref.volume, actual=str(actual.volume),
                message=f"Reference [{ref.reference_id}] declares volume {ref.volume} but the record shows {actual.volume}.",
            ))

        if ref.pages and actual.pages and ref.pages.strip() != str(actual.pages).strip():
            findings.append(Finding(
                check_id=10, severity="warning", reference_id=ref.reference_id,
                field="pages", declared=ref.pages, actual=str(actual.pages),
                message=f"Reference [{ref.reference_id}] declares pages {ref.pages} but the record shows {actual.pages}.",
            ))
    return findings


def check_16(
    doc: MetadataDoc,
    references: list[DeclaredReference],
    crossref_records: dict[int, ActualReference],
) -> list[Finding]:
    """
    Declared-vs-actual year (check 16, added per client request as a new
    check outside the original 12). A clean, deterministic equality
    comparison between the year declared in the reference and the resolved
    record's real publication year -- rule-based only, no LLM, no
    heuristics. Missing either side is "cannot compare", NOT a mismatch: a
    reference with no parseable declared year, or a record with no year, is
    silently skipped.

    Kept as its own check (its own line item in the report), separate from
    check 10's volume/issue/pages field diffs. A year mismatch marks the
    reference miscited -- report.py's _FIELD_COMPARISON_CHECK_IDS includes
    16 -- which is non-blocking for phase 2, like every other field diff.
    """
    findings = []
    for ref in references:
        actual = crossref_records.get(ref.reference_id)
        if actual is None or actual.status != "resolved" or actual.year is None:
            continue
        if ref.year is None:
            continue  # no declared year -> "cannot compare", not a mismatch
        if ref.year != actual.year:
            findings.append(Finding(
                check_id=16, severity="warning", reference_id=ref.reference_id,
                field="year", declared=str(ref.year), actual=str(actual.year),
                message=(
                    f"Reference [{ref.reference_id}] declares year {ref.year} but the "
                    f"resolved record's publication year is {actual.year}."
                ),
            ))
    return findings


def check_15(
    doc: MetadataDoc,
    references: list[DeclaredReference],
    crossref_records: dict[int, ActualReference],
) -> list[Finding]:
    """
    Retracted paper (check 15, the reserved ID the client kept free; now built
    on Crossref's free Retraction Watch integration -- no second provider).

    A reference is flagged when the RESOLVED record itself carries a retraction
    marker: Crossref injects an `updated-by` entry with type "retraction" (source
    "retraction-watch" or "publisher") into the retracted paper's own record,
    or an `update-to` retraction entry when the record is the notice. Both are
    parsed into ActualReference.retracted at fetch time, so this check costs
    ZERO extra API calls -- the record was already fetched for resolution.

    A retracted source is flagged critical and marks the reference miscited
    (report.py's _FIELD_COMPARISON_CHECK_IDS includes 15): a retracted paper
    cannot stand as a well-cited source, but like every other field diff,
    miscited never blocks phase 2.
    """
    findings = []
    for ref in references:
        actual = crossref_records.get(ref.reference_id)
        if actual is None or actual.status != "resolved" or not actual.retracted:
            continue
        findings.append(Finding(
            check_id=15, severity="critical", reference_id=ref.reference_id,
            field="retraction",
            actual=actual.retraction_notice or actual.doi,
            message=(
                f"Reference [{ref.reference_id}] cites a RETRACTED paper: the resolved record "
                f"{actual.doi} carries a retraction notice"
                + (f" ({actual.retraction_notice})" if actual.retraction_notice else "")
                + (f" registered by {actual.retraction_source}" if actual.retraction_source else "")
                + "."
            ),
        ))
    return findings


def check_11(
    doc: MetadataDoc,
    references: list[DeclaredReference],
    crossref_records: dict[int, ActualReference],
    contact_email: Optional[str] = None,
    journal_range_cache: Optional[dict[str, list[tuple[int, str]]]] = None,
) -> list[Finding]:
    """
    Volume within that journal's published range -- specific implementation
    of check 5, but checked against the REAL record (via ISSN) rather than
    only against other declared entries in this bibliography.

    Requires one additional Crossref call per distinct ISSN (Crossref has no
    direct "volume range" endpoint, so this samples recent records and
    approximates, per the spec's documented fallback) -- journal_range_cache
    lets that sample be reused across every reference citing the same
    journal in one run, rather than re-fetched per reference.

    Abstains (no finding) when the declared year falls outside the sampled
    range, rather than extrapolating -- a sample of ~20 records can't
    responsibly predict a volume number for a year well outside what was
    actually observed.
    """
    cache: dict[str, list[tuple[int, str]]] = journal_range_cache if journal_range_cache is not None else {}
    findings = []

    for ref in references:
        actual = crossref_records.get(ref.reference_id)
        if actual is None or actual.status != "resolved" or not actual.issn:
            continue
        if ref.year is None or not ref.volume or not ref.volume.isdigit():
            continue

        if actual.issn not in cache:
            cache[actual.issn] = fetch_journal_volume_samples(actual.issn, contact_email=contact_email)
        samples = cache[actual.issn]
        if len(samples) < 2:
            continue  # not enough data to approximate a range responsibly

        years = [y for y, _ in samples]
        if ref.year < min(years) or ref.year > max(years):
            continue  # declared year outside the sampled range -- abstain rather than extrapolate

        # Bracket the declared year with the closest sampled years on each side,
        # and check the declared volume falls within (a small tolerance around)
        # the volumes observed at those bracketing points.
        lower = max((y, v) for y, v in samples if y <= ref.year)
        upper = min((y, v) for y, v in samples if y >= ref.year)
        try:
            expected_low = min(int(lower[1]), int(upper[1])) - 1
            expected_high = max(int(lower[1]), int(upper[1])) + 1
        except (TypeError, ValueError):
            continue  # sampled volume isn't numeric -- can't bound it, abstain

        declared_volume = int(ref.volume)
        if not (expected_low <= declared_volume <= expected_high):
            findings.append(Finding(
                check_id=11, severity="warning", reference_id=ref.reference_id,
                field="volume", declared=ref.volume, actual=f"~{expected_low}-{expected_high}",
                message=(
                    f"Reference [{ref.reference_id}] declares volume {ref.volume} for {ref.year}, "
                    f"but this journal's sampled records suggest volume {expected_low}-{expected_high} "
                    "for that year."
                ),
            ))
    return findings
