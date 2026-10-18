# RefCheck — Bonifase hybrid adapter preparation

Status: **integration candidate only**. Judge Hybrid v2.2.2 is being evaluated on DEV by Zablon. The final production prompt has NOT been selected. No API calls or spending are authorized by this package.

## Provenance
The two prompts below are copied **byte for byte**, without edits, from the canonical prompt entries in `REFCHECK_HYBRID_MATERIAL_V2_2_2_PREFLIGHT_RESULTS_20261011.zip` (Zablon's zero-cost preflight). Their SHA-256 hashes are recorded in `SHA256SUMS.txt`. This package contains no DEV Gold, frozen inputs, test set, judge outputs, API credentials or prior run responses.

## Immediate implementation scope — offline only

1. **Wire the judge to the actual content path.** In `src/pipeline.py`, the existing content path currently invokes legacy `src/llm_wrapper.py::get_verdict`, while `src/judge_stage.py` is separate offline logic. Add a production-path adapter for Hybrid, preserving the existing metadata workflow. Keep prompt loading versioned/configurable, so the winning prompt can be swapped without rewriting the orchestration. Do not activate paid calls.
2. **Frozen shared evidence.** Construct one claim–cited-source evidence package and reuse exactly that package for all Luna and Sol votes. Route `sole` to the sole prompt, `one_of_several` to the multicitation prompt. An all-sources-supported multicitation outcome must not be represented as independently verified coverage of the whole sentence.
3. **Exact four-field judge contract.** Parse `evidence_sufficient` (boolean), `material_error_present` (boolean), `evidence_quotes` (array of strings), and `explanation` (string). Reject `evidence_sufficient=false` with `material_error_present=true`. Check every quoted passage against the delivered evidence. Derive per-vote supported / unsupported / uncertain in code, rather than trusting a separate model verdict field.
4. **Cascade.** Six Luna votes: clear only on six valid `supported`. All other cases escalate to six Sol votes. Sol's final supported or unsupported decision requires at least four votes for that outcome; otherwise uncertain. Invalid/failed votes never count as unsupported. Preserve the agreed quote-retry and transport-attempt constraints, per-call keys, the separate model identity and call ledger; no unauthorized paid calls.
5. **Correct report provenance.** The current `orchestrator/runner.py` emits `status=ok` even when it falls back to local citation parsing instead of running the judge. Record distinct job/file success, judge execution status, judge version/hash, number of calls and metadata status. Never display a locally parsed case as if it had received a scientific fidelity verdict.
6. **Do not use lexical overlap to decide fidelity.** `src/stage09_d01.py` can classify `increased` vs `decreased` as supported. Keep that fixture validator separate from the real judge. Do not attempt to make this token-overlap heuristic into the scientific judge.
7. **Offline acceptance evidence.** Execute end-to-end *mocked* tests through the actual content/report path, not just isolated validators: Luna 6/6 clearance, escalation, Sol 4/6 decisions, invalid ballot, literal-quote retry, no provider calls, no duplicate call keys, error handling, correct `judge_executed` reporting. Provide commands, logs, one sample report and `run_record.json`. Existing unrelated functionality should remain unchanged.

## Explicit exclusions
- No live API calls, no paid model requests, no TEST access, no new Gold labels.
- No real PaperPal PP01 run yet: the actual PP01 manuscript will be supplied separately.
- No unrelated refactoring, independent judge-prompt rewriting or extra features.
- Do not claim the integration is production-tested from offline fixtures alone. Report blockers explicitly.

Deliver an updated source ZIP or repository commit with a concise change summary and reproducible offline test output. Ask before work that would require paid API calls.

## Exact judge request input — no guessing

Construct **one input JSON object per claim–cited-source pair** with **exactly four top-level fields**, as required by BOTH unmodified v2.2.2 prompts:

```json
{
  "citation_context": "sole",
  "claim": "A citation-bearing sentence copied verbatim from the manuscript.",
  "evidence": [
    {"location": "abstract", "text": "Literal source passage copied from its abstract."}
  ],
  "source_type": "abstract"
}
```

This JSON is **illustrative structure only**, not a real scientific case or a labelled example. Enforce the following field vocabulary:

- `citation_context`: `sole` or `one_of_several`. Choose the matching sole/multicitation prompt. For multicitation, send a **separate pair for each cited source**, with evidence from that source only.
- `claim`: the **complete, verbatim** citation-bearing sentence, without paraphrase or claim reconstruction.
- `evidence`: array of `{ "location": "abstract" | "body", "text": string }` objects. The `text` field holds **literal retrieved source text**. Do not use section-specific location names (`methods`, `results`, etc.) or fabricated filler.
- `source_type`: `abstract` or `full_text` — **not `abstract_only`**. These are the exact values found in Zablon's 85 frozen v2.2.2 judge inputs. `source_type=full_text` does NOT by itself mean that all decisive evidence was delivered.

Freeze the shared input object before the first vote; use byte-identical scientific content for all six Luna votes and, if escalated, all six Sol votes. Send the exact selected prompt as the system message and `json.dumps(judge_input, indent=2, ensure_ascii=False)` as the user message. The example above should NOT be added to either judge prompt.

## Model request and validation settings

Use **OpenAI Chat Completions**, with a configurable model identity per arm:

| Property | Luna | Sol |
|---|---|---|
| Model | `gpt-5.6-luna` | `gpt-5.6-sol` |
| Timeout per request | 60 seconds | 300 seconds |
| Maximum completion tokens | 8,000 | 8,000 |
| SDK automatic retries (`max_retries`) | 0 | 0 |
| Temperature, seed | Omit both | Omit both |
| Reasoning parameter, `response_format` | Omit both | Omit both |

The Luna parameters follow the verified v2.2.2 zero-cost preflight. Sol's 300-second timeout is recorded in the v2.1 Sol runner. These settings are **target integration configuration**, not a claim that they have been executed in Bonifase's runtime. Keep API access disabled during this assignment.

For the four-field reply, enforce exact field names and types; reject invalid `evidence_sufficient=false, material_error_present=true`. Check that each `evidence_quotes` item is an **exact substring of one supplied evidence `text` block**, allowing only CRLF/CR-to-LF line-ending normalization. A vote with sufficient evidence must contain at least one nonempty literal quote. Permit **at most one extra provider attempt if and only if the quote is non-literal**, with a separately recorded attempt and call key. Do not silently retry schema failures; SDK automatic retry stays disabled. Preserve and audit the existing explicit transport-failure policy. Invalid votes occupy slots but never count as `unsupported`.

## Hour-estimate approval gate — before beginning additional billable work

Bonifase: **before starting this integration**, provide Eduardo with a written estimate of the hours required, the expected cost at the agreed hourly rate, the deliverables and any dependency/blocker. **Wait for Eduardo's explicit approval of the estimate and scope before beginning billable implementation.** Do not interpret receipt of this ZIP as spending authorization. If scope or estimate needs to grow, request approval before exceeding it. No one has approved paid API requests, TEST usage or a real PP01 run in this handoff.

