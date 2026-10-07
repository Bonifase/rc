# Metadata Acceptance Status

Date: 2026-09-15 (final package rebuild: 01:22 +03:00)

## Verification completed

- Metadata unit/integration suite: **101 passed**.
- Offline validation harness: **30/30 fixtures passed**.
- Harness breakdown: 17 caught fixtures, 8 executable gap fixtures reproduced as silent, and 5 synthetic/reconstructed capability fixtures passed. Six additional documented gaps are non-runnable in the offline harness.
- The harness used cached Crossref records from `validation/real_records.json`; check 11 network sampling was stubbed, so no paid or live API calls were made.
- The report and harness preserve the distinction between expected gaps and unexpected findings.

## Available hand-verified corpus

The repository contains validation material extracted from 7 documents:

- Doc1+2 (ChatGPT)
- Doc3 (Gemini)
- Doc5 (SciSpace/Claude catalog)
- Doc7 (Elicit)
- Doc8 (Consensus)
- Doc9 (Grok)
- Doc10 (Perplexity)

The specification requires 10 hand-verified documents. Final acceptance remains **open** until Eduardo approves a reduced corpus or the unavailable documents are recovered.

Executable fixture coverage in this package:

| Document | Coverage |
|---|---|
| Doc1 (ChatGPT) | executable fixtures |
| Doc2 (ChatGPT) | executable fixtures |
| Doc3 (Gemini) | executable fixtures |
| Doc4 (human baseline) | unavailable in recovered materials |
| Doc5 (SciSpace/Claude) | reconstructed document-specific fixture |
| Doc6 (SciSpace recovery report) | source report present, no executable metadata fixture |
| Doc7 (Elicit) | reconstructed document-specific fixture |
| Doc8 (Consensus) | executable fixtures |
| Doc9 (Grok) | reconstructed authorship-displacement fixture; wrong-article finding retained as a documented non-runnable gap |
| Doc10 (Perplexity) | reconstructed synthetic journal-container fixture; not real-record coverage |

## Known limitations

- Eight executable gap fixtures pass by reproducing the expected silence, and six additional catalog gaps are documented as non-runnable in `validation/REPORT.md`; these are known limitations, not hidden harness failures.
- Check 8 can flag legitimate shared co-authorship as displaced authorship. This is a documented precision risk; the implementation remains literal to the approved check definition.
- Check 11 is **deferred/not validated against live network sampling**. The harness uses an injected `journal_range_cache` for its synthetic capability fixture and stubs network sampling.
- The harness is offline and validates cached records. Any new documents must have their records cached and provenance recorded.

## Delivery status

The implementation is reproducible against the executable fixture corpus: **30/30 fixtures pass**. Final 10-document acceptance is intentionally **not claimed**; Doc4 is unavailable and Doc6 has no executable metadata fixture in the recovered set. Eduardo's decision is required on the reduced corpus or the missing coverage.
