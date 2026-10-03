"""
Phase C -- needs Phase B fully done (every reference resolved) before it
can run, since check 8 compares declared authors against the ACTUAL author
list of every OTHER reference.
"""
from src.metadata.document import MetadataDoc
from src.metadata.extraction import DeclaredReference
from src.metadata.crossref_metadata import ActualReference
from src.metadata.findings import Finding


def check_08(
    doc: MetadataDoc,
    references: list[DeclaredReference],
    crossref_records: dict[int, ActualReference],
) -> list[Finding]:
    """
    Displaced authorship: for each reference i, compare every declared
    author against the ACTUAL Crossref author list of every OTHER
    reference j != i. An exact surname + given-initial match in a
    reference that isn't its own is the finding -- per the spec, this is
    the field that breaks most often in the corpus (19 cases). O(n^2) over
    the bibliography; the spec notes 50 references = 2,500 comparisons,
    trivial in time, but only meaningful once every reference has a
    resolved actual record to compare against, hence Phase C.

    When found, the finding names both references involved so the author
    can be reassigned between them, per the spec's requirement.
    """
    # Pre-index each resolved reference's ACTUAL author list for fast lookup.
    actual_authors_by_ref: dict[int, list[tuple[str, str]]] = {}
    for ref_id, actual in crossref_records.items():
        if actual is not None and actual.status == "resolved":
            actual_authors_by_ref[ref_id] = [
                (a.family.lower(), a.given.strip()[0].lower())
                for a in actual.authors
                if a.family and a.given and a.given.strip()
            ]

    findings = []
    for ref in references:
        for author in ref.authors:
            if not author.family or not author.given or not author.given.strip():
                continue
            family_lower = author.family.lower()
            given_initial = author.given.strip()[0].lower()

            for other_ref_id, other_authors in actual_authors_by_ref.items():
                if other_ref_id == ref.reference_id:
                    continue
                if (family_lower, given_initial) in other_authors:
                    findings.append(Finding(
                        check_id=8,
                        severity="warning",
                        reference_id=ref.reference_id,
                        field="author",
                        declared=f"{author.family}, {author.given}",
                        actual=f"appears in reference [{other_ref_id}]'s actual author list, not [{ref.reference_id}]'s",
                        message=(
                            f"Declared author '{author.family}, {author.given}' of reference "
                            f"[{ref.reference_id}] matches an author in reference [{other_ref_id}]'s "
                            f"resolved record instead -- possible displaced authorship between "
                            f"[{ref.reference_id}] and [{other_ref_id}]."
                        ),
                    ))
    return findings


def check_14_author_omission(
    doc: MetadataDoc,
    references: list[DeclaredReference],
    crossref_records: dict[int, ActualReference],
) -> list[Finding]:
    """
    The substantive half of check 14's "authors omitted without 'et al.'"
    concern -- see phase_a.py's check_14 docstring for why this lives here
    instead of Phase A: it needs the real Crossref author count, which
    isn't available until resolution has run.

    Flags a reference whose declared author list is shorter than the
    resolved record's real author count, with NO truncation marker
    ("...", "et al.") indicating the omission was intentional -- i.e.
    authors silently dropped rather than properly abbreviated.
    """
    findings = []
    for ref in references:
        if ref.authors_truncated:
            continue  # declared truncation is fine -- that's what the marker is for
        actual = crossref_records.get(ref.reference_id)
        if actual is None or actual.status != "resolved":
            continue
        declared_count = len(ref.authors)
        actual_count = len(actual.authors)
        if declared_count > 0 and actual_count > declared_count:
            findings.append(Finding(
                check_id=14,
                severity="warning",
                reference_id=ref.reference_id,
                field="authors",
                declared=str(declared_count),
                actual=str(actual_count),
                message=(
                    f"Reference [{ref.reference_id}] declares {declared_count} author(s) but the "
                    f"resolved record has {actual_count}, with no '...' or 'et al.' marker indicating "
                    "the omission was intentional."
                ),
            ))
    return findings
