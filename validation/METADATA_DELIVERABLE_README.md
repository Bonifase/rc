# MVP1 Metadata Module Deliverable

## Contents

- `src/metadata/`: metadata extraction, resolution, report orchestration, and checks.
- `src/doi_utils.py`, `src/citation_parser.py`, `src/config.py`: required internal dependencies.
- `tests/test_metadata_*.py`: metadata unit and integration tests.
- `validation/`: offline validation harness, cached records, fixtures, report, and acceptance status.
- `validation/requirements.txt`: package dependencies for clean extraction.

## Verification

- Metadata tests: 101 passed.
- Offline validation harness: 30/30 fixtures passed.
- Executable coverage includes real-record fixtures from the recovered corpus, reconstructed Doc5/Doc7 fixtures, a reconstructed Doc9 authorship-displacement fixture, and a reconstructed synthetic Doc10 capability fixture. The Doc9 wrong-article finding remains documented but non-runnable because the offline harness does not exercise the required resolution path.
- Doc4 is unavailable in the recovered materials; Doc6 has no executable metadata fixture.
- The required 10-document acceptance remains open pending client decision.
- Check 11 is deferred because live journal-range sampling is not run by the offline harness.
- No paid or live API calls were made.

The known documented gaps and check 8 shared-authorship precision risk are recorded in `validation/REPORT.md` and `validation/METADATA_ACCEPTANCE_STATUS.md`.

No secrets, `.env` files, chat logs, or credentials are included.
