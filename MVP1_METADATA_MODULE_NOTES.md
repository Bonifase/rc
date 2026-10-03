# MVP1 Metadata Module — Build Notes

Read this alongside `MVP1_Metadata_Module_Spec.txt`. Covers the two
decisions the spec flagged for input, one real precision issue found
through testing, and every place the implementation is intentionally
narrower than the spec's full ambition (with why).

---

## Decision 1 — Check 1, repurposed

Original check 1 catches author-year citation keys (e.g. two different
"Smith 2019"s) resolving ambiguously without an a/b suffix. That specific
ambiguity structurally can't happen in this project's numbered+DOI format
— two different papers get two different numbers, full stop.

**Built as:** the numbered-format analogue of the same underlying problem —
two different reference *numbers* pointing at the same resolved DOI (a real
data-entry bug: a paper accidentally listed twice, a copy-paste error).
Zero API calls, since it only needs the declared DOIs. See
`src/metadata/checks/phase_a.py::check_01`.

## Decision 2 — Check 14's semantic half

The module's whole value proposition (per the spec's own "why this
matters") is zero tokens, zero LLM dependency. Check 14 bundles a
rule-based sub-check (authors omitted without "et al.") with a genuinely
semantic one (declared screening criterion vs. how it was actually
applied).

**Built as:** the rule-based half is fully implemented, split across two
places for a reason worth knowing about (see the deviation below). The
semantic half is **not implemented** — it's surfaced explicitly in every
report's `not_evaluated_checks` list, rather than silently missing, so its
absence is visible rather than a silent gap someone finds by accident
later.

---

## One documented deviation from the spec's execution-order table

The spec lists check 14 under **Phase A (zero API, zero cost)**. But
meaningfully detecting "authors were silently dropped without a truncation
marker" requires knowing the *real* author count — that only exists after
Crossref resolution (Phase B/C), which contradicts zero-API.

Rather than fake a zero-API version of that comparison, it's split:
- **Phase A** (`phase_a.py::check_14`): only what's genuinely checkable from
  the declared text alone — a truncation marker ("...", "et al.") with
  zero named authors before it is malformed on its face, regardless of the
  real count.
- **Phase C** (`phase_c.py::check_14_author_omission`): the substantive
  comparison (declared author count vs. the real Crossref count when no
  truncation marker is present), colocated with check 8 since both need
  the same resolved author data.

---

## A real precision issue, found through testing — worth validating before trusting this as-is

Ran a dry-run against three real references from this project's own test
manuscript (Lien et al. 2016, Robertson et al. 2017, Gundappa et al.
2022 — all salmonid genome duplication papers from an overlapping research
group: Sandve, Lien, Macqueen, and others co-author several of them).

**Check 8, implemented exactly as specified** ("compare EVERY declared
author of EVERY reference against the ACTUAL author list of every OTHER
reference — an exact surname+initial match in a reference that isn't its
own is the finding") **fired repeatedly** — not because any citation was
actually wrong, but because these are real co-authors who legitimately
published multiple different papers together, several of which happen to
be cited in the same document. That's normal in any narrow-topic
literature review, not an error.

**This is not a bug in the implementation** — it's doing exactly what the
spec describes. It's a precision characteristic of the check's definition
that's worth knowing about before trusting its output at face value:

- On a bibliography citing **topically diverse, unrelated authors**, an
  author's name legitimately appearing in two different references *is* a
  strong signal something's wrong — check 8 should work close to as
  intended.
- On a bibliography **heavily citing one overlapping research group**
  (common in narrow-subfield reviews, exactly like the manuscript this
  project already uses for testing), expect real noise — possibly a lot of
  it — from ordinary shared co-authorship, not displaced authorship.

**Recommended before shipping this check as-is:** run it against the 10
hand-verified documents per the acceptance criteria and look specifically
at check 8's false-positive rate. If the real corpus is topically diverse,
this may be a non-issue matching the spec's own reported "19 real cases."
If it's not, worth considering a tighter signal before trusting the
output — e.g., only flagging when the reference's *entire* declared author
list also fails to match its *own* actual record (suggesting a wholesale
mix-up, not just one shared co-author), or weighting a match down when the
same author already appears legitimately across several references. Not
implemented here, since the spec asks for the check as literally
described, and softening it without seeing the real corpus risks silently
suppressing real findings.

---

## Scope notes on the deliberately narrower checks

A few checks in Phase A tackle a well-defined, mechanically checkable
*subset* of what the spec describes, rather than the full open-ended
version — because the full version is a language-understanding problem
that would require an LLM, contradicting the module's zero-token design.
Each is documented in its own docstring; summarized here:

- **Check 2** (partition coherence): only catches a stated count sitting
  immediately before a single bracket group ("five studies [1-4]"). Won't
  catch a count stated in a different sentence, or a multi-clause split
  ("20 of the 45 studies... the remaining 25...").
- **Check 3** (declared vs. actual reference count): only fires when
  exactly one unambiguous "N studies/references/articles/..." phrase is
  found in the body text. Abstains — rather than guesses — when there's
  zero or more than one candidate phrase.
- **Check 4** (same figure cited differently): only catches the same
  reference number cited with different *percentage* values in different
  places. Won't catch inconsistent counts, ratios, or absolute values, or
  a restated number with no nearby citation marker.
- **Check 11** (volume within journal's published range): approximates
  from a ~20-record sample per the spec's own documented fallback ("if the
  API has no such view, approximate from the year↔volume relationship"),
  since Crossref has no direct range endpoint. Abstains when the declared
  year falls outside the sampled range, rather than extrapolating.

## Reference-string parsing (3a extraction)

Targets the APA-style pattern actually used throughout this project's own
sample/test documents (`Family, F. F., ... (Year). Title. Journal,
Volume(Issue), Pages. DOI`). It is not a general-purpose citation parser —
Vancouver, MLA, and Chicago styles will parse partially at best. Any field
the regex can't confidently extract is left `None`, never guessed at or
left as `""` — per the spec's explicit null-vs-empty-string requirement,
since a downstream check comparing a `None` declared field against a real
actual field should register as "cannot compare," not as a mismatch.

## Integration status (update)

This module now IS wired into both entry points. `src/analysis_runner.py`
runs `process_sentences` (content) and `build_report` (metadata)
concurrently on two threads — they're still code-independent (no shared
HTTP session, no shared retry logic, per the spec's "no dependency on v16"
requirement), but the app-layer callers (`run.py`, `webapp/backend/app.py`)
now run both and merge the results into one report:
`bibliography_status`, `document_level_findings`, and
`not_evaluated_checks` are merged onto the same report dict that already
carries `sentences`/`summary`.

A metadata failure is caught at the `analysis_runner` level and surfaced
as `report["metadata_error"]` rather than raising — the primary content
verdicts always reach the caller even if Crossref is having a bad day.

The React UI (`webapp/frontend/src/App.jsx`) renders this as a new
"Reference citation check" panel below the sentence verdicts:
`BibliographySection` and `BibliographyEntry` components, styled with the
same status-color vocabulary `ResultCard` already used for sentence
verdicts (no new color system introduced). `/api/recheck` intentionally
does NOT touch metadata — see its docstring in `app.py` for why.

CLI input (`run.py`) and `/api/analyze`'s direct-JSON input have no raw
bibliography text to extract structured fields from, so metadata checks
needing journal/volume/author data have less to compare against on those
two paths specifically — resolution still runs off the DOI alone.
`/api/analyze-text` (the normal webapp flow, pasted free text) has the
full raw bibliography text and gets the complete extraction.

Now covered by an actual integration test suite:
`tests/test_analysis_runner.py` (the shared concurrent-execution helper),
plus new cases in `tests/test_api.py` and `tests/test_run.py` proving the
merged report shape end-to-end, including that a metadata failure doesn't
take down content results.
