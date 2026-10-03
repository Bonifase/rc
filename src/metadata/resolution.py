"""
REFERENCE RESOLUTION FLOW (runs once per reference, determines its status).
Implements the spec's flowchart exactly:

    Resolve against Crossref by DOI. If the reference has NO DOI, or the
    DOI does not resolve at all, resolve by declared title + first author
    instead. Then:

    Not found by either route
      -> status: not_found (BLOCKS phase 2)

    DOI resolves, but to a different article than declared
      -> attempt rescue: search by declared title + first author
         - paper not found          -> wrong_article, unrecoverable (BLOCKS)
         - found, title similarity is low -> synthesized_title (does NOT block)
         - found and matches well   -> proceed to field-by-field comparison

    Record found, title similarity low
      -> synthesized_title (does NOT block)

    Record found and matches
      -> field-by-field comparison (src/metadata/checks/phase_b.py, checks
         9/10/11, using the record already fetched here -- zero extra API
         calls)
         - all match     -> well_cited
         - some mismatch -> miscited (does NOT block)

RULE THAT MUST NOT BE BROKEN (per the spec): only not_found, unrecoverable
wrong_article, and technical_failure block phase 2. miscited NEVER blocks
it -- the paper was located, so content comparison proceeds regardless of
metadata problems.
"""
from dataclasses import dataclass
from typing import Optional

from src.metadata.extraction import DeclaredReference
from src.metadata.crossref_metadata import (
    ActualReference,
    fetch_by_doi,
    search_by_title_author,
    title_similarity,
    CrossrefMetadataTransientError,
)

TITLE_SIMILARITY_THRESHOLD = 0.85  # per the spec's implementation note on check 7

BLOCKING_STATUSES = {"not_found", "wrong_article", "technical_failure"}


@dataclass
class ResolvedReference:
    """
    status is one of: "well_cited" | "miscited" | "synthesized_title" |
    "wrong_article" | "not_found" | "technical_failure" -- OR the internal-
    only marker "_needs_field_comparison", meaning resolution found a
    matching record but hasn't yet run the field-by-field comparison
    (checks 9/10/11 in src/metadata/checks/phase_b.py) that decides
    well_cited vs. miscited. Callers must run that comparison and finalize
    the status before this ever reaches a report -- report.py's
    build_report() enforces this and will raise if it sees the internal
    marker, rather than silently leaking it into output.
    """
    reference_id: int
    status: str
    actual: Optional[ActualReference] = None  # None when not_found / wrong_article / technical_failure
    usable_identifier: Optional[str] = None
    blocks_phase_2: bool = False
    detail: str = ""  # human-readable explanation of how this status was reached, for the report / debugging


def _matches_declared(declared: DeclaredReference, actual: ActualReference) -> tuple[bool, float]:
    """
    Check 7's core comparison: does the resolved record's title match the
    declared title (and, as a supporting signal, the first author's
    surname)? Returns (matches_well, similarity_score).
    """
    if not declared.title or not actual.title:
        # Nothing to compare -- treat as a non-match rather than assume
        # agreement on missing data. Downstream, this routes into the
        # rescue/search path, which is the safer default here.
        return False, 0.0

    score = title_similarity(declared.title, actual.title)
    matches = score >= TITLE_SIMILARITY_THRESHOLD
    return matches, score


def resolve_reference(declared: DeclaredReference, contact_email: Optional[str] = None) -> ResolvedReference:
    """
    Resolves one reference through the full flow. Never raises: a transient
    network/API failure that can't be resolved after retries (already
    handled inside fetch_by_doi/search_by_title_author's own retry policy)
    comes back as status="technical_failure", which blocks phase 2 for that
    reference the same way not_found does -- the pipeline genuinely
    couldn't determine anything, as distinct from determining that the
    reference IS a real problem.
    """
    first_author_family = declared.authors[0].family if declared.authors else None

    # --- Route selection: DOI first, unless there's no DOI to try. ---
    actual: Optional[ActualReference] = None
    if declared.doi:
        actual = fetch_by_doi(declared.doi, contact_email=contact_email)
        if actual.status == "error":
            return ResolvedReference(
                declared.reference_id, status="technical_failure", blocks_phase_2=True,
                detail=f"DOI lookup failed after retries: {actual.error_message}",
            )
        if actual.status == "not_found":
            actual = None  # falls through to the title+author route below, same as "no DOI"

    if actual is None:
        # No DOI, or the DOI didn't resolve -- resolve by declared title + first author instead.
        search_result = search_by_title_author(declared.title or "", first_author_family, contact_email=contact_email)
        if search_result.status == "error":
            return ResolvedReference(
                declared.reference_id, status="technical_failure", blocks_phase_2=True,
                detail=f"Title/author search failed after retries: {search_result.error_message}",
            )
        if search_result.status == "not_found":
            return ResolvedReference(
                declared.reference_id, status="not_found", blocks_phase_2=True,
                detail="No DOI (or DOI did not resolve), and no matching record found by title + author search.",
            )
        # Found something by title+author search -- apply the threshold here.
        if search_result.title_match_score is not None and search_result.title_match_score < TITLE_SIMILARITY_THRESHOLD:
            return ResolvedReference(
                declared.reference_id, status="synthesized_title", actual=search_result,
                usable_identifier=search_result.doi or None, blocks_phase_2=False,
                detail=f"Best title/author match has similarity {search_result.title_match_score:.2f}, below the {TITLE_SIMILARITY_THRESHOLD} threshold.",
            )
        return ResolvedReference(
            declared.reference_id, status="_needs_field_comparison", actual=search_result,
            usable_identifier=search_result.doi or None,
            detail="Resolved by title + author search (no usable DOI).",
        )

    # --- DOI resolved to *something*. Does it match the declared paper? (check 7) ---
    matches, score = _matches_declared(declared, actual)
    if matches:
        return ResolvedReference(
            declared.reference_id, status="_needs_field_comparison", actual=actual,
            usable_identifier=actual.doi, detail=f"DOI resolved and title matches declared (similarity {score:.2f}).",
        )

    # DOI resolves, but to a different article than declared -- attempt rescue.
    rescue = search_by_title_author(declared.title or "", first_author_family, contact_email=contact_email)
    if rescue.status == "error":
        return ResolvedReference(
            declared.reference_id, status="technical_failure", blocks_phase_2=True,
            detail=f"Rescue search failed after retries: {rescue.error_message}",
        )
    if rescue.status == "not_found":
        return ResolvedReference(
            declared.reference_id, status="wrong_article", blocks_phase_2=True,
            usable_identifier=actual.doi,
            detail=(
                f"Declared DOI resolves to a different paper (title similarity {score:.2f}), "
                "and no rescue match was found by title + author search."
            ),
        )
    if rescue.title_match_score is not None and rescue.title_match_score < TITLE_SIMILARITY_THRESHOLD:
        return ResolvedReference(
            declared.reference_id, status="synthesized_title", actual=rescue,
            usable_identifier=rescue.doi or actual.doi, blocks_phase_2=False,
            detail=(
                f"Declared DOI resolves to a different paper (similarity {score:.2f}); rescue search's "
                f"best candidate also has low similarity ({rescue.title_match_score:.2f})."
            ),
        )
    return ResolvedReference(
        declared.reference_id, status="_needs_field_comparison", actual=rescue,
        usable_identifier=rescue.doi,
        detail=f"Declared DOI pointed to the wrong paper; rescued by title + author search (similarity {rescue.title_match_score:.2f}).",
    )
