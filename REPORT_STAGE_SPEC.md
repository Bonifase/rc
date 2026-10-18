# RefCheck report stage

Status: OFFLINE-VALIDATED HANDOFF. The report-stage integration in Bonifase's motor and live Luna-generated explanations have not been tested. Build with mock replies; no live API calls in this phase.
All reports are in English (`lang: "en"`, `report_language: "en"`).

## 1. Where the stage runs

The stage runs after the final verdict of each claim–source pair (Luna 6/6 clearance or Sol 4/6 decision). The judge Hybrid v2.2.2 and its prompts stay unchanged.

- Report-text call: one call per claim–source pair with final verdict `unsupported` whose reference has `show_content = true` (section 7.2).
- Pairs with final verdict `supported` or `uncertain`: no call. They are recorded in `run_record.json` and are not rendered.
- The report-text output never changes the verdict. The verdict code never reads it.

## 2. Model settings

Same settings as Luna in the judge.

| Property | Value |
|---|---|
| Model | `gpt-5.6-luna` |
| API | OpenAI Chat Completions |
| System message | `prompts/report_text_v2.txt`, byte for byte |
| User message | `json.dumps(report_input, indent=2, ensure_ascii=False)` |
| Timeout | 60 seconds |
| `max_completion_tokens` | 8,000 |
| `max_retries` (SDK) | 0 |
| Temperature, seed, reasoning parameter, `response_format` | Omit all |

Load the prompt by path and record its SHA-256 (see `SHA256SUMS.txt`), as with the judge prompts.

## 3. Input (one JSON object per pair)

| Field | Source |
|---|---|
| `report_language` | Always `"en"` |
| `citation_context` | Same value sent to the judge |
| `marker` | Citation marker of this source in the sentence, for example `[2]` |
| `claim` | Same verbatim sentence sent to the judge |
| `source_type` | Same value sent to the judge |
| `evidence` | Same frozen evidence array sent to the judge |
| `judge_quotes` | `evidence_quotes` of the Sol votes that voted `unsupported`, deduplicated, in vote order |
| `judge_explanations` | `explanation` of those same votes |
| `error_types` | The eight `content` entries of the template catalog, as `{key, label, description}` in English (`fixtures/content_error_types_en.json`) |

Examples: `fixtures/case_02_input.json` (case [2] of the template, full text) and `fixtures/synthetic_abstract_input.json` (the same passage labelled as an abstract, synthetic test data for the abstract path). In both, every statement and number of the mock reply is supported by the input evidence.

## 4. Output (exactly five fields)

```json
{
  "error_type": "source_says_opposite",
  "claim_marked": "sentence with **marked** parts",
  "evidence_passage": "literal excerpt with **marked** parts",
  "evidence_context": "summary of the study, or null",
  "observation": "text for the author"
}
```

`error_type` is always one of the eight content keys: `source_says_opposite`, `study_did_not_do_that`, `data_mismatch`, `inflated_figure`, `figure_out_of_context`, `measure_confused`, `overstated_certainty`, `source_does_not_say`. `source_does_not_say` applies to any unsupported claim, so a valid type always exists. Null is invalid.

## 5. Validation in code

`reference/validate_report_text.py` implements the structural and literal checks. Port it or reuse it. Semantic truth of model-written prose cannot be checked by code; review the first live outputs.

1. Exact input contract (claim, marker, evidence with `abstract`/`body` locations, source type, the eight content types, judge quotes literal in the evidence) and exactly five output fields.
2. `error_type` is a string and one of the eight keys. Any other value, including null, a list or an object, is invalid and never raises an exception.
3. `claim_marked` without `**` is identical to `claim` (CRLF/CR to LF only). One to three marked parts. The citation marker is not marked.
4. `evidence_passage` without `**` is a continuous substring of one evidence `text`. One to three marked parts. Maximum 160 words.
5. `evidence_context` is null when `source_type` is `full_text`, and a non-empty string without `**` when it is `abstract`.
6. `observation` is a non-empty string without `**`.
7. Every number written in `observation` or `evidence_context` appears in the evidence or in the claim.

## 6. Retry, failure and DRAFT

- An invalid reply gets one more attempt, with its own call key. Transport failures follow the judge's existing policy.
- Maximum two attempts per pair.
- The report is `report_complete: false` only in these cases:
  1. A pair fails validation twice (`report_text_failed`).
  2. A reference has a status the mapping does not know (`blocks_final`).
  3. A rendered case lacks `reference_id`, or `references_reviewed` is smaller than the number of distinct affected references.
- When `report_complete` is false, the HTML is written only as `Informe_Ref_Check_<document_id>_DRAFT.html`, the API never serves it as final, and `run_record.json` lists every cause.
- Nothing else produces a DRAFT. References not found in Crossref, technical failures and heuristic signals are recorded internally and the report is final.

## 7. Mapping into the template

### 7.1 Content cases

For each `unsupported` pair of a reference with `show_content = true`, one content case in `cases`:

| Template field | Value |
|---|---|
| `reference_id` | Bibliography entry id of the cited reference |
| `marker` | `marker` |
| `kind` | `"content"` |
| `source_label` | Author-year label of the reference |
| `error_type` | Output `error_type` |
| `claim` | Output `claim_marked` |
| `evidence` | Output `evidence_passage` |
| `evidence_context` | Output `evidence_context` (omit when null) |
| `passage_origin` | `full_text` if any evidence block has `location: body`, otherwise `abstract` |
| `observation` | Output `observation` |
| `reference`, `doi` | From the parsed reference entry |
| `rewrite` | Not set |

Unsupported pairs of a reference with `show_content = false` are recorded in `run_record.json` with their verdict and are not rendered.

### 7.2 Metadata decisions

Metadata comes from `src.metadata.report.build_report()` in `RefCheck-latest-3.zip`, without a model call. Run `reference/metadata_status_mapping.py`:

`build_metadata_plan(metadata_output, declared_dois)` where `declared_dois` is `{reference_id: declared DOI or None}` from the parsed bibliography. It returns one `ReferenceDecision` per bibliography entry, the orphan-citation cards, the internal-only findings, `references_reviewed` and `blocks_final`.

| Module status and findings | Shown to the client | Content cards of this reference | Counts in `references_reviewed` |
|---|---|---|---|
| `well_cited` / `miscited` with direct field findings (9 title, 10 journal/volume/pages, 16 year, phase-C 14 authors) | `wrong_reference_details` card | Shown | Yes |
| `well_cited` / `miscited` where the declared DOI differs from `usable_identifier` (rescued by title search) | DOI added to the `wrong_reference_details` card | Shown | Yes |
| `well_cited` / `miscited` with check 15 | `retracted_article` card | Shown | Yes |
| `well_cited` / `miscited` with check 7 (declared first author not in the record) | Nothing | Not shown | No |
| `wrong_article` | `doi_leads_elsewhere` card | None exist (phase 2 blocked) | Yes |
| `not_found`, `synthesized_title`, `technical_failure` | Nothing | Not shown | No |
| `well_cited` / `miscited` with only checks 1, 4, 5, 8, 11, phase-A 14, or 13 with a `reference_id` (never cited) | Nothing | Shown | Yes |
| Document-level check 13 without `reference_id` (marker with no entry) | `citation_without_reference` card, one per cited number | Not applicable | No |
| Document-level checks 2, 3 | Nothing | Not applicable | Not applicable |
| Unknown status | Nothing | Not shown | No, and `blocks_final` |

`fabricated_reference` is never assigned by the engine. Everything not shown is recorded in `run_record.json` with the status, the reason and every finding. `not_evaluated_checks` are recorded as not assessed.

### 7.3 Metadata card fields

Common fields: `reference_id`, `marker` (`[n]` of the reference), `kind: "metadata"`, `source_label` (author-year), `error_type`, `claim` (text of the first sentence that cites the reference), `doi`. The suggestion comes from the catalog; do not set `rewrite`.

| `error_type` | `reference_as_generated` | `reference_official` | `observation` | `findings` |
|---|---|---|---|---|
| `wrong_reference_details` | Declared reference string, with `**` around each declared value of the card's findings | Reference built from the Crossref record in the same citation style, with `**` around each record value | "The cited article exists. These details do not match the official record:" | One line per finding: "The correct {field} is {record value}, not {declared value}." For authors: "The official record lists {record count} authors; the reference lists {declared count}." |
| `retracted_article` | Declared reference string | Reference built from the Crossref record | "The cited article was retracted." | "Crossref registers a retraction notice for this article: {notice}." |
| `doi_leads_elsewhere` | Declared reference string, with `**` around the DOI | `null` | "The DOI in the reference belongs to a different article." | "The DOI {doi} does not lead to the cited article." Set `doi_dead: true` so no link points to the other article. |
| `citation_without_reference` | "**[n]** is not in the reference list." | `null` | Not set | "[n] is cited in {k} sentence(s) and does not appear in the reference list." Use `reference_id: "orphan:n"`, `count_as_reference: false`, `source_label: "Not in the reference list"`, no `doi`. |

A reference with two shown problems (for example wrong details and a retraction) gets two cards with the same `reference_id`. It counts once.

### 7.4 Counting

- `references_reviewed` = `build_metadata_plan(...)["references_reviewed"]`. Never infer it from the cards.
- "References with metadata problems" = distinct `reference_id` among metadata cards, excluding `count_as_reference: false`.
- "M contain errors" = distinct `reference_id` among all cards, excluding `count_as_reference: false`.
- "Citations the source does not support" = number of content cards.
- Every rendered card carries `reference_id`. The template stops rendering when `references_reviewed` is missing or smaller than M.

The template retains its visual layout. It uses the frozen 8-content + 4-metadata English-only catalog, requires reference_id on every rendered case, and never displays a generic "No matching record" statement for a DOI pointing elsewhere or an orphan citation. The included sample HTML has no fabricated-reference card. The embedded sample PDF has been regenerated from that sample and must be freshly generated for each real report.

## 8. Call ledger

Each report-text call is a separate ledger row with stage `report_text`, its own call key, the model, the prompt hash, the input hash, the attempt number, the tokens and the cost. It never shares a key with a judge vote.

## 9. Offline tests required

1. Mock replies `fixtures/case_02_mock_reply.json` (full text) and `fixtures/synthetic_abstract_mock_reply.json` (abstract) pass validation and render a complete content card.
2. `fixtures/case_02_invalid_reply.json` (altered claim) and `fixtures/synthetic_abstract_invalid_reply.json` (invented numbers) fail validation, trigger one retry, and a second invalid reply produces `report_text_failed`, `report_complete: false` and a `_DRAFT` file.
3. Null, list, object or unknown `error_type` is invalid without an exception. The two new content types are valid.
4. No report-text call for `supported` or `uncertain` pairs, or for references with `show_content = false`.
5. The verdict is identical with and without the report stage.
6. Every row of the table in 7.2 (see `reference/test_report_stage.py`).
7. Counting, including a citation without a reference that is shown but not counted (`reference/test_template_counts.cjs`).
8. Zero provider calls.

## 10. Scope

This ZIP contains the final template, the report-text prompt, mock inputs, reference implementations and offline tests. It does not modify Bonifase's repository. Offline tests verify deterministic mapping and formatting only; they do not validate model-written prose or a live end-to-end report.

Known limit of the metadata module, not changed here because `src/` is not modified: check 7 accepts the declared first author when the name appears anywhere in the record's author list.


## 11. Independent acceptance gate (mandatory before FINAL)

- Run `python -m unittest discover -s reference -p test_report_stage.py` (23 regression tests).
- Run `python reference/test_independent_acceptance.py` (27 additional structural, negative-case, PDF and JavaScript checks); needs `bs4`, `node` and `pdftotext` in the test environment.
- Run `node reference/test_template_counts.cjs` (distinct-reference tally, orphan exclusion, invalid totals).
- Run `python reference/validate_render_data.py RefCheck_Report_Ultima_Plantilla.html` for the example. Import and call `validate_render_data(data, catalog)` on each *generated* report before publishing it as FINAL; if any issue is returned, publish only a DRAFT with a reason in `run_record.json`.
- Serialize report-data JSON safely inside HTML (`</script>` and other HTML script terminators escaped), and use the approved catalog from this package. Do not silently add or override labels.
- The embedded PDF belongs to the example only: regenerate the PDF from the actual final HTML of each manuscript rather than carrying over the sample PDF.

All provider calls are prohibited in this handoff. The passing offline acceptance gate does not prove Luna's explanations are scientifically correct or that Bonifase's real pipeline produces the same outputs; those need the subsequent PP01 end-to-end integration test.

## 12. Consolidated offline acceptance (2026-10-12)

The report-text prompt accepts **English only**. The HTML renderer HTML-escapes
quotes in attributes, restricts DOI/source links to safe URL formats, and does
not permit content or other metadata findings to exclude a reference from the
reviewed-reference count. The server-side validator rejects malformed or
unhashable category values without crashing, invalid catalogue labels, duplicate
card IDs and incorrect `count_as_reference: false` flags.

A runnable synthetic end-to-end acceptance test is included in
`reference/test_end_to_end_offline.py`. It calls the actual reference metadata
mapping and report-text response validator, constructs a synthetic report from
those results, validates the report data, renders HTML in Chromium, exercises
filter/expand interactions, generates a fresh PDF, verifies the PDF's text and
embeds that exact PDF in the generated report. It also submits hostile
HTML/script and URL inputs and verifies that they do not execute.

Commands (from the extracted ZIP root, with Python, bs4, Playwright/Chromium,
Node and `pdftotext` installed):

```
python -m unittest discover -s reference -p test_report_stage.py
python reference/test_independent_acceptance.py
node reference/test_template_counts.cjs
python reference/validate_render_data.py RefCheck_Report_Ultima_Plantilla.html
python reference/test_end_to_end_offline.py
```

`offline_end_to_end.html`, `.pdf` and `.png` are **synthetic examples**, not
PP01 or production outputs. The sample embedded PDF in
`RefCheck_Report_Ultima_Plantilla.html` was freshly regenerated using
`reference/regenerate_sample_pdf.py` and matches the current sample wording.

**What this verifies:** deterministic schema, mapping, supported output
categories, counts, HTML behavior in the Chromium environment used here, fresh
PDF generation from synthetic report data, integrity, and input hardening.

**What remains outside this ZIP:** the actual Bonifase pipeline integration,
real source retrieval, live Luna-generated explanations, live provider billing,
and the real PP01 end-to-end run. The integration owner must test those in the
actual deployment; do not infer them from these offline results. All calls to
paid models are prohibited for this handoff.

## 12. Required publication contract (corrected, 12 Oct 2026)

**Do not publish a FINAL report from the generic sample HTML or from
`validate_render_data(data, catalog)` alone.** That method checks the presentation
schema but does not independently establish source provenance. The production
exporter MUST call `reference/validate_render_data.py::validate_for_publication`
with five arguments:

- `data` is the fully assembled report with `report_complete: true` only after
  every required component has succeeded; the template independently rejects
  `report_complete != true`.
- `catalog` is the exact 8-content/4-metadata English catalog in the HTML.
- `manifest` is the complete frozen manuscript bibliography, cited sentences
  and orphan markers; its schema appears in the validator's docstring.
- `official_records` is the resolved Crossref record snapshot collected by the
  metadata stage **during the same run**. Do not construct it by manually
  supplying a plausible official reference or searching a different paper.
- `expected_manifest_sha256` is the SHA-256 recorded at intake of the
  canonical manifest, serialized with sorted keys, `ensure_ascii=False`, and
  compact separators. A changed or absent hash blocks FINAL.

Validation fails if the content claim, source excerpt or observation is empty,
if a metadata card lacks its declared source or findings, or if a reported
reference/claim/marker is not in the frozen manuscript manifest. It also fails
when a supposedly verified official citation cannot be regenerated exactly
from its structured Crossref fields. A model response must independently pass
`validate_report_text.validate` against its exact frozen evidence *before*
the content card is assembled.

### 12.1 Preserve original metadata resolution

Bonifase's original `src.metadata.report.build_report` does not return the
resolved Crossref record, despite having it in its local `resolved_by_id`
variable. The tested local `integration_candidate/report.py` shows the minimal
source-side addition: call `records_from_resolution(references, resolved_by_id,
title_similarity)` from `reference/official_provenance.py` and include the
result under `official_records` in `build_report()` output. This reuses the
record already retrieved; **no additional Crossref request or model call**.
The original report function's status/fields are otherwise preserved.

The metadata card assembler `reference/render_metadata_cards.py` consumes that
output, the real parsed bibliography and the frozen citation manifest. It
never fills missing official entries with synthetic prose. Source identity
must be established by title matching (the resolver's threshold) **and the
same first author** before it automatically displays bibliographic-field
mismatches. Unknown/non-indexed references remain internal as approved.

For a wrong declared DOI rescued to the actual cited article, the metadata
card retains the *declared wrong DOI in the generated reference*, but any
clickable source link uses the **resolved correct DOI**. A `wrong_article`
card has no clickable DOI and does not show the other paper as official.
Content cases must also link to the resolved article, never to a known wrong
DOI. A reference not matched to its official paper does not automatically
publish content or bibliographic corrections.

### 12.2 Acceptance performed offline

The following new tests extend, without replacing, the original regressions:

- `python reference/test_publication_gate.py` tests required fields,
  publication completeness, tampering and frozen-manifest checks.
- `PYTHONPATH=reference python reference/test_same_run_provenance.py`
  tests all four metadata categories, source identity, wrong DOI rescue and
  negative provenance cases.
- `PYTHONPATH=reference python reference/test_real_metadata_module_offline.py`
  runs the **original Bonifase metadata checks** and local candidate source
  adapter, with Crossref resolution mocked and unavailable HTTP retry
  dependency stubbed for offline testing, then renders that same output in
  Chromium and generates its PDF.

The existing offline browser test is `PYTHONPATH=reference python
reference/test_end_to_end_offline.py` (synthetic, with PDFs freshly generated).
This is **not** a claim of production-runtime compatibility, live Crossref
connectivity, valid live Luna-generated explanations, or a real PP01 run.
Those require the fully integrated production runtime and explicit permission
for any paid calls.
