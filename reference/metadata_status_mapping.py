"""Deterministic translation from the metadata module output to report cards.

Input: the dict returned by src.metadata.report.build_report() in
RefCheck-latest-3.zip ("bibliography_status", "document_level_findings",
"not_evaluated_checks"), plus the declared DOI of each reference from the
parsed bibliography. No model calls, no network.

What the client sees (decided by Eduardo, 2026-10-12):
  wrong_reference_details      title, journal, volume, pages, year, authors or DOI differ
                               from the record of the cited article
  retracted_article            the cited article has a registered retraction notice
  doi_leads_elsewhere          the declared DOI belongs to a different article
  citation_without_reference   a marker in the text has no entry in the reference list
Everything else is recorded internally only and never shown: references not
found in Crossref, technical failures, uncited entries, heuristic signals.
"""
from dataclasses import dataclass, field

SHOWN_METADATA_TYPES = frozenset({"wrong_reference_details", "retracted_article",
                                  "doi_leads_elsewhere", "citation_without_reference"})

# Direct comparisons of a declared field against the record of the cited article.
DIRECT_FIELDS = {
    9: frozenset({"title"}),
    10: frozenset({"journal", "volume", "pages"}),
    16: frozenset({"year"}),
    14: frozenset({"authors"}),  # phase-C omission check: both counts present
}
RETRACTION_CHECK = 15
IDENTITY_CHECK = 7      # declared first author absent from the record's authors
ORPHAN_CHECK = 13       # reference_id None = marker without entry; with id = never cited


@dataclass(frozen=True)
class Card:
    error_type: str
    findings: tuple


@dataclass(frozen=True)
class ReferenceDecision:
    reference_id: object
    status: str
    reviewed: bool          # counts in references_reviewed
    show_content: bool      # content cards of this reference may be rendered
    cards: tuple            # metadata cards rendered for the client
    internal_findings: tuple  # recorded in run_record.json only
    reason: str
    blocks_final: bool = False


def _is_direct(f):
    if not isinstance(f, dict):
        return False
    check, fld = f.get("check_id"), f.get("field")
    if type(check) is not int or fld not in DIRECT_FIELDS.get(check, ()):
        return False
    d, a = f.get("declared"), f.get("actual")
    return (d is not None and str(d).strip() != "" and a is not None and str(a).strip() != ""
            and str(d).strip() != str(a).strip())


def _norm_doi(doi):
    if not doi:
        return ""
    d = str(doi).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.strip()


def classify_reference(entry, declared_doi=None, official_record=None):
    """Classify one bibliography_status entry. Never raises on a known status."""
    if not isinstance(entry, dict) or not isinstance(entry.get("status"), str):
        raise ValueError("bibliography_status entry with a string status required")
    if entry.get("reference_id") is None:
        raise ValueError("reference_id required")
    findings = entry.get("findings")
    if not isinstance(findings, list) or any(not isinstance(f, dict) for f in findings):
        raise ValueError("findings must be a list of dicts")
    if any(f.get("reference_id") not in (None, entry["reference_id"]) for f in findings):
        raise ValueError("finding tied to another reference")
    rid, status = entry["reference_id"], entry["status"]
    allf = tuple(findings)

    if status == "technical_failure":
        return ReferenceDecision(rid, status, False, False, (), allf, "lookup_failed")
    if status == "not_found":
        return ReferenceDecision(rid, status, False, False, (), allf, "not_found_in_crossref")
    if status == "synthesized_title":
        # Closest record has a different title: the cited article was not found.
        # The judge may have compared the claim against that other record, so
        # content cards of this reference are not shown either.
        return ReferenceDecision(rid, status, False, False, (), allf, "cited_article_not_found")
    if status == "wrong_article":
        doi = entry.get("usable_identifier") or declared_doi or ""
        f = {"check_id": "doi", "field": "doi", "declared": doi, "actual": None,
             "message": f"The DOI {doi} does not lead to the cited article."}
        return ReferenceDecision(rid, status, True, False,
                                 (Card("doi_leads_elsewhere", (f,)),), allf, "doi_mismatch")
    if status not in ("well_cited", "miscited"):
        return ReferenceDecision(rid, status, False, False, (), allf, "unknown_status", True)
    if official_record is not None:
        if not isinstance(official_record, dict):
            return ReferenceDecision(rid,status,False,False,(),allf,"malformed_official_record",True)
        if (official_record.get('verified_title_match') is not True or
                official_record.get('verified_first_author_match') is not True):
            return ReferenceDecision(rid,status,False,False,(),allf,"article_identity_unconfirmed")
        if not official_record.get('official_ref_text'):
            return ReferenceDecision(rid,status,False,False,(),allf,"official_record_incomplete")

    # well_cited / miscited: the cited article was found in Crossref with title
    # similarity >= 0.85 and the field checks compared against that record.
    if any(f.get("check_id") == IDENTITY_CHECK for f in findings):
        # Identity in doubt: nothing about this reference is shown, content included.
        return ReferenceDecision(rid, status, False, False, (), allf, "identity_not_confirmed")

    direct = [f for f in findings if _is_direct(f)]
    usable = _norm_doi(entry.get("usable_identifier"))
    declared = _norm_doi(declared_doi)
    if declared and usable and declared != usable:
        # Rescued by title + author search: the article exists with another DOI.
        direct.append({"check_id": "doi", "field": "doi", "declared": declared_doi,
                       "actual": entry.get("usable_identifier"),
                       "message": "The DOI in the reference differs from the DOI of the cited article."})
    retraction = [f for f in findings if f.get("check_id") == RETRACTION_CHECK]
    shown_ids = {id(f) for f in direct + retraction}
    internal = tuple(f for f in findings if id(f) not in shown_ids)

    cards = []
    if direct:
        cards.append(Card("wrong_reference_details", tuple(direct)))
    if retraction:
        cards.append(Card("retracted_article", tuple(retraction)))
    reason = "shown" if cards else ("internal_only" if internal else "no_finding")
    return ReferenceDecision(rid, status, True, True, tuple(cards), internal, reason)


def orphan_citation_cards(document_level_findings):
    """One citation_without_reference card per distinct cited number.

    Returns (cards, internal): cards is a list of (number, findings tuple).
    """
    if not isinstance(document_level_findings, list):
        raise ValueError("document_level_findings must be a list")
    by_number, internal = {}, []
    for f in document_level_findings:
        if (isinstance(f, dict) and f.get("check_id") == ORPHAN_CHECK
                and f.get("reference_id") is None and f.get("field") == "citation_marker"
                and f.get("declared") not in (None, "")):
            by_number.setdefault(str(f["declared"]), []).append(f)
        else:
            internal.append(f)
    cards = [(n, tuple(v)) for n, v in sorted(by_number.items(), key=lambda kv: (len(kv[0]), kv[0]))]
    return cards, tuple(internal)


def build_metadata_plan(metadata_output, declared_dois=None, official_records=None):
    """Classify a whole build_report() output.

    declared_dois: {reference_id: declared DOI string or None} from the parsed bibliography.
    """
    if not isinstance(metadata_output, dict):
        raise ValueError("metadata_output must be a dict")
    refs = metadata_output.get("bibliography_status")
    if not isinstance(refs, list):
        raise ValueError("bibliography_status must be a list")
    ids = [r.get("reference_id") if isinstance(r, dict) else None for r in refs]
    if any(i is None for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("missing or duplicate reference_id")
    declared_dois = declared_dois or {}
    decisions = []
    for r in refs:
        rid=r['reference_id']
        record=official_records.get(str(rid)) if isinstance(official_records,dict) else None
        dec=classify_reference(r, declared_dois.get(rid), record)
        # No official record for a supposedly resolved paper is a code/data
        # integration defect, not a user-facing bibliographic error.
        if official_records is not None and r['status'] in ('well_cited','miscited') and record is None:
            dec=ReferenceDecision(rid,r['status'],False,False,(),tuple(r['findings']),
                                  'missing_same_run_record',True)
        if official_records is not None and r['status']=='wrong_article':
            if record is None or record.get('verified_wrong_doi') is not True:
                dec=ReferenceDecision(rid,r['status'],False,False,(),tuple(r['findings']),
                                      'wrong_article_provenance_missing',True)
        decisions.append(dec)
    orphan_cards, doc_internal = orphan_citation_cards(metadata_output.get("document_level_findings", []))
    not_evaluated = metadata_output.get("not_evaluated_checks", [])
    if not isinstance(not_evaluated, list):
        raise ValueError("not_evaluated_checks must be a list")
    return {
        "decisions": decisions,
        "orphan_cards": orphan_cards,
        "document_internal_findings": doc_internal,
        "not_evaluated_checks": not_evaluated,
        "references_reviewed": sum(1 for d in decisions if d.reviewed),
        "blocks_final": [d.reference_id for d in decisions if d.blocks_final],
    }
