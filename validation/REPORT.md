# Metadata module vs. error-catalog corpus — coverage report

Validates the metadata module (`src/metadata/checks/phase_{a,b,c}.py`) against the
the frozen metadata validation set, combining preserved real-record cases with
explicitly labeled reconstructed document-specific fixtures. Preserved reusable
records remain cached in `validation/real_records.json`.

Ground truth for every reusable case is a live Crossref record cached in
`validation/real_records.json` (19 verified `resolved` records).

Run: `py -3.14 validation/run_harness.py` → 30/30 fixtures pass (offline:
check_11's per-ISSN sample is stubbed; it is exercised only via injected
`journal_range_cache`).

## Results at a glance

| kind | count | meaning |
|---|---|---|
| caught | 17 | module's checks fire on the catalog error (often with additional correct secondary catches) |
| gap | 8 | executable fixtures in which the module remains silent on the documented error |
| synthetic | 5 | reconstructed/document-specific capability fixtures or generic controls |

Plus 6 non-runnable catalog gaps listed in `DOCUMENTED_GAPS`.

## Executable coverage by document

| document | executable coverage |
|---|---|
| Doc1 (ChatGPT) | yes |
| Doc2 (ChatGPT) | yes |
| Doc3 (Gemini) | yes |
| Doc4 (human baseline) | unavailable in recovered materials |
| Doc5 (SciSpace/Claude) | reconstructed structural never-cited fixture |
| Doc6 (SciSpace recovery report) | report present, no executable metadata fixture |
| Doc7 (Elicit) | reconstructed partition-coherence fixture |
| Doc8 (Consensus) | yes |
| Doc9 (Grok) | reconstructed authorship-displacement fixture; wrong-article finding retained as a documented non-runnable gap |
| Doc10 (Perplexity) | reconstructed synthetic journal-container fixture; not real-record coverage |

The specification's 10-document acceptance criterion remains open. Check 11 is
deferred: the harness only uses an injected journal range cache and does not make
live network sampling calls.

## Caught cases (17)

| fixture | catalog source | error | checks that fire |
|---|---|---|---|
| doc1_a2_4_duplicate_doi | Doc1 A2.4+A2.6 | same paper under two numbers, two fabricated author lists | 1 (dup DOI), 7 (both fabricated first authors), 16 (year 2022 vs 2024 on ref 84) |
| doc3_b4_9_15_shared_url | Doc3 B4 #9/#15 | two declared refs share one real DOI | 1, 7 (both fabricated authors), 14 (silent omission), 16 (both declared 2020 vs 2024) |
| doc1_a2_1_pereira_partial | Doc1 A2.1 | fabricated author + wrong year/volume/artno; title paraphrase sim 0.815 | 10 (volume 12 vs 11, pages 24326 vs 16466), 16 (year 2022 vs 2021) — author missed (see gaps) |
| doc2_a4_1_naya_venus_caught | Doc2 A4.1 | fabricated first author, wrong article number, title verbatim | 7, 10 (pages 22901 vs 16466), 14 |
| doc2_a4_2_nornberg_caught | Doc2 A4.2 | fabricated authors, wrong vol (225) + pages | 7, 10 |
| doc3_b4_6_doyle_partial | Doc3 B4 #6 | declared Doyle over Tipsmark record; year contradiction | 7, 14, 16 (year 2022 vs 2009) |
| doc3_b4_27_xie_partial | Doc3 B4 #27 | declared Xie over Levesque record; year contradiction | 7, 16 (year 2001 vs 2008) |
| doc2_a4_5_duran_displaced_caught | Doc2 A4.4+A4.5 | displaced authorship (Fernández-Borràs on wrong ref) + fabricated list | 7, 8 (displaced), 14 (both entries silently truncated) |
| doc3_b4_19_picha_pages_caught | Doc3 B4 #19 | wrong pages 167-183 vs 196-211 | 10 |
| doc2_a4_3_serrana_volume_caught | Doc2 A4.3 | wrong volume 10 vs 9, wrong year | 10, 16 (year 2019 vs 2018) (+ tolerated spurious 7, see limitations) |
| doc2_a5_orphan_nevercited | Doc2 A5 | orphan marker [75]; never-cited 7/10/73 | 13 both directions (+ tolerated spurious 8, see limitations) |
| doc3_b1_orphaned_biblio | Doc3 B1 | whole APA bibliography orphaned from body markers | 13 |
| doc3_b1_29_zhong_omission_caught | Doc3 B4/B1 #29 | 3 declared authors, no et al., 4 real | 14 |
| doc1_a2_6_retracted_caught | synthetic on live-verified record | citation of a RETRACTED paper (10.1155/2021/5554487) | 15 (retraction) (+ tolerated 9, `[Retracted]` title prefix — see limitations) |
| doc5_structural_never_cited | Doc5 (SciSpace/Claude), reconstructed from documented finding | two bibliography entries are never cited in the body | 13 (+ tolerated 5) |
| doc7_partition_range_error | Doc7 (Elicit), reconstructed from documented finding | declared five/four/two partition conflicts with expanded citation ranges | 2 |
| doc9_displaced_authorship_reconstructed | Doc9 (Grok), reconstructed from documented Macedo/Fappi/Fiorenza finding | authorship displaced across the two resolved records | 7, 8, 14 |

## Gap cases (8) — executable cases in which the module remains silent

| fixture | mechanism of the miss |
|---|---|
| doc8_declared_count_gap | check_03's count-phrase regex is English-only (`studies\|references\|articles\|papers\|trials\|reports`); "los 50 artículos más relevantes" / "50 Included" never match → 50-vs-37 discrepancy invisible |
| doc1_a2_2_weng_zhong_gap | fabricated author on a real DOI whose declared title is a loose paraphrase (sim 0.500 < 0.85): check_07's gate never opens |
| doc3_b4_7_elizur_gap | fabricated author + fabricated title together (sim 0.371): identity is unverifiable, nothing fires |
| doc3_b4_30_zohar_gap | same C2 pattern as Elizur (sim 0.590) |
| doc1_a2_3_doc3_12_lema_gap | last author promoted to first author of his own paper: check_08 only cross-checks OTHER references, and Lema IS in this record's real list; check_07's gate also closed (sim 0.711) |
| doc2_a4_6_fuentes_title_gap | altered title tail drops similarity to 0.759, below check_09's 0.85 floor → non-verbatim title unflagged |
| doc8_ambiguous_author_year_keys | check_01 was repurposed for the numbered+DOI format; author-year keys ("Fuentes 2013/2011", "Vélez 2017") are structurally invisible to it |
| doc8_koganti_year_gap | check_16 covers this exact pattern (declared 2020 vs a real 2021 record WOULD fire), but here the real record was never located (declared-only fixture, no stable DOI) — the residual miss is record resolution, not the year comparison; volume 11 is internally plausible for 2020, so check_11 also abstains |

### Closed gap: check 16 — declared-vs-actual year (added per client request)
New check, outside the original 12 (ID 16), deterministic equality comparison,
rule-based only (no LLM, no heuristics). It is its own line item in the
report, separate from check 10's volume/issue/pages. Missing declared year or
missing record year = "cannot compare", never a mismatch. A mismatch marks the
reference miscited (non-blocking for phase 2). Before this check, Doyle
(2022 vs 2009), Xie (2001 vs 2008), Serrana (2019 vs 2018), Pereira (2022 vs
2021), the duplicate-DOI (Zhao 2022 vs 2024) and shared-URL (both 2020 vs 2024)
cases all carried an unflagged year error — the single most repeatable miss in
the corpus. All six are now caught by check_16.

## Added: check 15 — retracted paper (client-requested, free data source)
New check on the ID the client kept reserved (15). Built on Crossref's free
Retraction Watch integration (Crossref acquired the RW database in 2023; no
second provider, no extra cost): a retracted paper's own Crossref record
carries an `updated-by` entry with `type: "retraction"` (source
`retraction-watch` or `publisher`), and a retraction notice carries the mirror
`update-to` entry. `crossref_metadata._extract_retraction` parses both into
`ActualReference.retracted` at fetch time, so check_15 costs ZERO extra API
calls — the record was already fetched for resolution.

Severity is critical and a retracted reference is marked miscited (added to
`report.py`'s `_FIELD_COMPARISON_CHECK_IDS`): a retracted paper cannot stand as
a well-cited source, but like every other field diff, miscited never blocks
phase 2. Verified live against the API: `fetch_by_doi` on a retracted record
(10.1155/2021/5554487 → notice 10.1155/2024/9753878, source retraction-watch)
returns `retracted=True`; a clean record returns `retracted=False`.

## Synthetic/reconstructed capability fixtures (5)

| fixture | proves |
|---|---|
| synthetic_check_03_english_count | check_03 fires on its supported English phrases (isolates the Doc8 miss to language coverage) |
| synthetic_check_11_volume_range | check_11's year-bracketing logic end to end (inline record + injected cache) |
| synthetic_check_14_malformed_truncation | phase-A half: "et al." with no named author is malformed |
| doc3_b1_3_berishvili_clean | negative control: a fully correct entry produces zero findings across all 15 check functions |
| doc10_container_substitution | reconstructed Doc10 capability case for journal/volume/pages substitution; not real-record Doc10 coverage |

## Documented gaps that cannot currently be turned into runnable fixtures (6)

`doc9_wrong_article_identifiers_gap`, `doc8_truncated_locators`,
`doc3_b2_off_domain_sources`, `doc3_b4_11_wrong_pmc_url`,
`doc1_a2_5_doc2_a3_2_orphan_claims`, and
`doc2_a2_7_cross_document_inconsistency`.

The Doc9 wrong-article finding is deliberately non-runnable here: the offline
harness does not exercise the DOI-resolution path that establishes
`wrong_article`, and placeholder journal metadata can spuriously trigger check
10 without testing the documented Doc9 error.
Printed at the end of every harness run so they are not silently forgotten.

## Module limitations surfaced by the harness (not catalog errors)

1. **check_07 is accent-sensitive** (`doc2_a4_3_serrana_volume_caught`): declared
   "García de la Serrana" (accented, as any document writes it) is compared by
   exact lowercase string against Crossref's unaccented "Garcia de la Serrana",
   producing a spurious critical finding on an otherwise correct entry.
2. **check_09 fires on `[Retracted]`-prefixed record titles**
   (`doc1_a2_6_retracted_caught`): Crossref keeps the publisher's "[Retracted]"
   prefix in the retracted record's title, which drags the declared-vs-actual
   similarity to ~0.95 — below check_09's 0.97 verbatim bar — so a non-verbatim
   title finding appears alongside the retraction finding. It is a quirk of the
   retraction marker, not a transcription error.
3. **check_08 has a shared-authorship false positive** (`doc2_a5_orphan_nevercited`):
   when two different papers legitimately share an author (Pérez-Sánchez,
   Calduch-Giner on both the ref [7] entry and the Naya-Català record), check_08
   reports displaced authorship because it never verifies the declared author
   against the reference's OWN resolved record.
4. **check_14 fires on any silently-truncated list** even when the catalog's
   named error is something else (e.g. fabricated authors in a 2-of-4 list). This
   is correct behavior — the module catches real secondary errors — but fixtures
   must account for it in `expected_checks`.

## How the verdicts are computed

Per fixture, `expected_checks` must all fire; `unexpected_ok` may fire;
anything else is a SURPRISE and fails the fixture. Gap fixtures expect `[]`
(a pass means the module genuinely stays silent).
