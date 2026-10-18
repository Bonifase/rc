"""
3c — THE REPORT'S THIRD SECTION, plus the orchestration that gets there:
extraction -> resolution (per reference) -> Phase A/B/C checks -> one
bibliography-status entry per reference, findings deduplicated onto it.

RULE THAT MUST NOT BE BROKEN (per the spec): only "not_found", unrecoverable
"wrong_article", and "technical_failure" block phase 2 (content comparison).
"miscited" NEVER blocks it. build_report() enforces this in code (not just
by convention) via BLOCKS_PHASE_2, so a future bug elsewhere can't silently
start blocking on miscited.
"""
from dataclasses import dataclass
from src.metadata.official_provenance import records_from_resolution
from typing import Optional

from src.metadata.document import MetadataDoc
from src.metadata.extraction import DeclaredReference, expand_citation_markers
from src.metadata.crossref_metadata import ActualReference
from src.metadata.resolution import resolve_reference, BLOCKING_STATUSES
from src.metadata.findings import Finding
from src.metadata.checks import phase_a, phase_b, phase_c

# Check IDs whose findings (when present for a reference already routed to
# "_needs_field_comparison") mean that reference is miscited, not well_cited.
# 15 = retracted paper (critical, non-blocking), 16 = declared-vs-actual year.
_FIELD_COMPARISON_CHECK_IDS = {7, 9, 10, 11, 15, 16}


@dataclass
class ReferenceStatus:
    reference_id: int
    status: str
    usable_identifier: Optional[str]
    findings: list[dict]
    used_in_sentences: list[str]
    blocks_phase_2: bool

    def to_dict(self) -> dict:
        return {
            "reference_id": self.reference_id,
            "status": self.status,
            "usable_identifier": self.usable_identifier,
            "findings": self.findings,
            "used_in_sentences": self.used_in_sentences,
            "blocks_phase_2": self.blocks_phase_2,
        }


# The screening-criterion half of check 14 needs semantic judgment (a model
# call) and is explicitly NOT implemented in this build -- see
# checks/phase_a.py's check_14 docstring. Surfaced here, once, at the
# document level (it's a whole-review concern, not really tied to any one
# reference) rather than silently absent from the report.
NOT_EVALUATED_CHECKS = [
    {
        "check_id": 14,
        "component": "screening_criterion",
        "reason": (
            "Requires semantic judgment (comparing the declared inclusion/exclusion "
            "criteria against how they were actually applied) -- not implemented in "
            "this zero-LLM build. The rule-based half of check 14 (authors omitted "
            "without 'et al.') IS implemented."
        ),
    }
]


def _used_in_sentences(reference_id: int, sentences: list[dict]) -> list[str]:
    used = []
    for sentence in sentences:
        if reference_id in expand_citation_markers(sentence["text"]):
            used.append(sentence["id"])
    return used


def build_report(
    doc: MetadataDoc,
    references: list[DeclaredReference],
    contact_email: Optional[str] = None,
) -> dict:
    """
    Runs the full metadata module end to end and returns:

    {
      "bibliography_status": [ {reference_id, status, usable_identifier,
                                 findings, used_in_sentences, blocks_phase_2}, ... ],
      "document_level_findings": [ ... ],  # findings not tied to one reference (checks 2, 3, 13-orphaned)
      "not_evaluated_checks": [ ... ],
    }

    A reference cited by five sentences appears ONCE in bibliography_status,
    per the spec -- findings are attached to the reference, not duplicated
    per citing sentence.
    """
    # --- Resolve every reference (the one Crossref/search call per ref). ---
    resolved_by_id = {}
    journal_range_cache: dict[str, list[tuple[int, str]]] = {}
    for ref in references:
        resolved_by_id[ref.reference_id] = resolve_reference(ref, contact_email=contact_email)

    crossref_records: dict[int, ActualReference] = {
        ref_id: r.actual for ref_id, r in resolved_by_id.items() if r.actual is not None
    }

    # --- Run every check. Each returns list[Finding]; collect them all. ---
    all_findings: list[Finding] = []
    all_findings += phase_a.check_01(doc, references, crossref_records)
    all_findings += phase_a.check_02(doc, references, crossref_records)
    all_findings += phase_a.check_03(doc, references, crossref_records)
    all_findings += phase_a.check_04(doc, references, crossref_records)
    all_findings += phase_a.check_05(doc, references, crossref_records)
    all_findings += phase_a.check_13(doc, references, crossref_records)
    all_findings += phase_a.check_14(doc, references, crossref_records)

    all_findings += phase_b.check_07(doc, references, crossref_records)
    all_findings += phase_b.check_09(doc, references, crossref_records)
    all_findings += phase_b.check_10(doc, references, crossref_records)
    all_findings += phase_b.check_16(doc, references, crossref_records)
    all_findings += phase_b.check_15(doc, references, crossref_records)
    all_findings += phase_b.check_11(doc, references, crossref_records, contact_email=contact_email, journal_range_cache=journal_range_cache)

    all_findings += phase_c.check_08(doc, references, crossref_records)
    all_findings += phase_c.check_14_author_omission(doc, references, crossref_records)

    findings_by_ref: dict[int, list[Finding]] = {}
    document_level_findings: list[Finding] = []
    for finding in all_findings:
        if finding.reference_id is None:
            document_level_findings.append(finding)
        else:
            findings_by_ref.setdefault(finding.reference_id, []).append(finding)

    # --- Finalize status: resolve "_needs_field_comparison" into well_cited/miscited. ---
    bibliography_status = []
    for ref in references:
        resolved = resolved_by_id[ref.reference_id]
        ref_findings = findings_by_ref.get(ref.reference_id, [])

        if resolved.status == "_needs_field_comparison":
            has_field_comparison_finding = any(f.check_id in _FIELD_COMPARISON_CHECK_IDS for f in ref_findings)
            final_status = "miscited" if has_field_comparison_finding else "well_cited"
        else:
            final_status = resolved.status

        blocks = final_status in BLOCKING_STATUSES
        assert not (final_status == "miscited" and blocks), (
            "miscited must never block phase 2 -- see the module docstring's 'RULE THAT MUST NOT BE BROKEN'"
        )

        bibliography_status.append(ReferenceStatus(
            reference_id=ref.reference_id,
            status=final_status,
            usable_identifier=resolved.usable_identifier,
            findings=[f.to_dict() for f in ref_findings],
            used_in_sentences=_used_in_sentences(ref.reference_id, doc.sentences),
            blocks_phase_2=blocks,
        ).to_dict())

    from src.metadata.crossref_metadata import title_similarity
    return {
        "official_records": records_from_resolution(references, resolved_by_id, title_similarity),
        "bibliography_status": bibliography_status,
        "document_level_findings": [f.to_dict() for f in document_level_findings],
        "not_evaluated_checks": NOT_EVALUATED_CHECKS,
    }
