"""
Runs the metadata module's checks against the validation corpus
(validation/fixtures.py) and reports, per fixture, which catalog error cases
the module catches, which are documented gaps, and which synthetic capability
fixtures prove a given check fires.

Verdict rules per fixture:
  - every check_id in `expected_checks` MUST produce at least one Finding
  - check_ids in `unexpected_ok` MAY fire (tolerated secondary catches / known
    module quirks)
  - any fired check_id in neither is a SURPRISE and fails the fixture, because
    the corpus author did not anticipate it (and the report must make it
    visible, not silently absorb it)

check_11's per-ISSN Crossref sample is stubbed to return nothing so the
harness runs fully offline -- fixtures exercise check_11 via the injected
journal_range_cache only.

Usage:  py -3.14 validation/run_harness.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The corpus contains non-cp1252 characters (full-width brackets, accented
# names); the console codepage must not kill the report mid-print.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from src.metadata.document import MetadataDoc
from src.metadata.extraction import Author, build_declared_references
from src.metadata.crossref_metadata import ActualReference
from src.metadata.checks.phase_a import check_01, check_02, check_03, check_04, check_05, check_13, check_14
from src.metadata.checks.phase_b import check_07, check_09, check_10, check_11, check_15, check_16
from src.metadata.checks.phase_c import check_08, check_14_author_omission
import src.metadata.checks.phase_b as _phase_b

from validation.fixtures import FIXTURES, DOCUMENTED_GAPS

# Offline mode: check_11 may never hit Crossref from the harness. Only the
# injected journal_range_cache fixtures get exercised.
_phase_b.fetch_journal_volume_samples = lambda *args, **kwargs: []


def record_to_actual(record: dict) -> ActualReference:
    """Converts a real_records.json dict (or inline dict) into an ActualReference."""
    authors = [
        Author(family=a.get("family"), given=a.get("given"))
        for a in record.get("authors") or []
    ]
    volume = record.get("volume")
    issue = record.get("issue")
    return ActualReference(
        doi=record.get("doi", ""),
        status=record.get("status", "resolved"),
        title=record.get("title"),
        authors=authors,
        year=record.get("year"),
        journal=record.get("journal"),
        volume=str(volume) if volume is not None else None,
        issue=str(issue) if issue is not None else None,
        # Crossref article numbers (no page range) land in `pages` so the
        # field-diff checks compare declared vs real locators uniformly.
        pages=record.get("pages") or record.get("article_number"),
        issn=record.get("issn"),
        publication_type=record.get("publication_type"),
        retracted=bool(record.get("retracted")),
        retraction_notice=record.get("retraction_notice"),
        retraction_source=record.get("retraction_source"),
    )


def load_actual_records(actual_records: dict) -> dict[int, ActualReference]:
    from validation.fixtures import _REAL
    loaded = {}
    for ref_id_str, value in actual_records.items():
        record = _REAL[value] if isinstance(value, str) else value
        loaded[int(ref_id_str)] = record_to_actual(record)
    return loaded


ALL_CHECKS = [
    ("check_01", check_01),
    ("check_02", check_02),
    ("check_03", check_03),
    ("check_04", check_04),
    ("check_05", check_05),
    ("check_07", check_07),
    ("check_08", check_08),
    ("check_09", check_09),
    ("check_10", check_10),
    ("check_15", check_15),
    ("check_16", check_16),
    ("check_11", check_11),
    ("check_13", check_13),
    ("check_14", check_14),
    ("check_14_author_omission", check_14_author_omission),
]


def run_fixture(fixture: dict) -> tuple[set[int], list]:
    """Returns (fired_check_ids, findings)."""
    doc = MetadataDoc(sentences=fixture["sentences"], body_text=fixture["body_text"])
    references = build_declared_references(fixture["references"])
    records = load_actual_records(fixture.get("actual_records", {}))
    range_cache = fixture.get("journal_range_cache", {})

    findings = []
    for _, check in ALL_CHECKS:
        if check is check_11:
            findings.extend(check_11(doc, references, records, journal_range_cache=dict(range_cache)))
        else:
            findings.extend(check(doc, references, records))

    fired = {f.check_id for f in findings}
    return fired, findings


def verdict(fixture: dict, fired: set[int]) -> tuple[bool, set[int], set[int]]:
    expected = set(fixture["expected_checks"])
    allowed = expected | set(fixture.get("unexpected_ok", []))
    missing = expected - fired
    surprises = fired - allowed
    return (not missing and not surprises), missing, surprises


def main() -> int:
    passed = 0
    failed = 0
    print("=" * 100)
    print("VALIDATION HARNESS -- metadata module vs error catalog corpus")
    print("=" * 100)
    for fixture in FIXTURES:
        fired, findings = run_fixture(fixture)
        ok, missing, surprises = verdict(fixture, fired)
        status = "PASS" if ok else "FAIL"
        passed += ok
        failed += (not ok)

        print(f"\n[{status}] {fixture['id']}  (kind={fixture['kind']})")
        print(f"    source:     {fixture['source']}")
        print(f"    summary:    {fixture['summary']}")
        print(f"    expected:   {sorted(fixture['expected_checks'])}"
              + (f"  (+ok {sorted(fixture['unexpected_ok'])})" if fixture.get("unexpected_ok") else ""))
        print(f"    fired:      {sorted(fired)}")
        if missing:
            print(f"    MISSING:    {sorted(missing)}  <- expected but did not fire")
        if surprises:
            print(f"    SURPRISE:   {sorted(surprises)}  <- fired but not expected/tolerated")
        for f in sorted(findings, key=lambda f: (f.check_id, f.reference_id or 0)):
            ref = f"[{f.reference_id}]" if f.reference_id is not None else "[doc]"
            print(f"        c{f.check_id:02d} {f.severity:<8} {ref:<7} field={f.field}: {f.message}")

    print("\n" + "=" * 100)
    print(f"SUMMARY: {passed}/{len(FIXTURES)} fixtures pass")
    for kind in ("caught", "gap", "synthetic"):
        group = [f for f in FIXTURES if f["kind"] == kind]
        ok_group = [f for f in group if verdict(f, run_fixture(f)[0])[0]]
        print(f"  {kind:<9} {len(ok_group)}/{len(group)}")
    print("=" * 100)

    print("\nDOCUMENTED GAPS (cannot be turned into runnable fixtures):")
    for gap in DOCUMENTED_GAPS:
        print(f"  - [{gap['id']}] {gap['summary']}")
        print(f"      note: {gap['notes']}")

    print()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
