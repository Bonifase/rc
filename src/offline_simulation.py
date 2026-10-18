"""Opt-in offline simulation of the judge and report-text stages from the spec fixtures.

Never a scientific verdict: every pair is tagged as simulated, and the runner
refuses to publish a simulated report as FINAL.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.citation_parser import parse_citation_markers
from src.hybrid_judge_adapter import run_hybrid_luna_sol_cascade
from src.pipeline import PairResult, resolve_reference_numbers_for_sentence
from src.report_text_stage import unsupported_sol_votes_from_judge_payload

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE_NAMES = ("case_02", "synthetic_abstract")


def _load_fixture(name: str) -> dict[str, Any]:
    def read(suffix: str) -> dict[str, Any]:
        return json.loads((FIXTURES_DIR / f"{name}_{suffix}.json").read_text(encoding="utf-8"))

    return {"name": name, "input": read("input"), "reply": read("mock_reply")}


def _mark_claim(claim: str, marker: str) -> str:
    """Mark the claim body and leave the citation marker unmarked, as the validator requires."""
    if marker in claim:
        before, after = claim.split(marker, 1)
        lead = before.rstrip()
        if lead:
            return f"**{lead}**{before[len(lead):]}{marker}{after}"
    return f"**{claim}**"


def fixture_report_text_provider(**kwargs: Any) -> dict[str, Any]:
    """Stand-in for the report-text model: the fixture reply whose evidence matches the request."""
    report_input = kwargs["report_input"]
    # Both fixtures share one passage, so source_type must be part of the match.
    evidence = [(block["location"], block["text"]) for block in report_input["evidence"]]
    for name in FIXTURE_NAMES:
        fixture = _load_fixture(name)
        fixture_evidence = [(block["location"], block["text"]) for block in fixture["input"]["evidence"]]
        if fixture_evidence != evidence or fixture["input"]["source_type"] != report_input["source_type"]:
            continue
        reply = dict(fixture["reply"])
        if report_input["claim"] != fixture["input"]["claim"]:
            reply["claim_marked"] = _mark_claim(report_input["claim"], report_input["marker"])
        return reply
    raise ValueError("no fixture matches the supplied evidence")


class _FixtureVoteProvider:
    """Judge-vote stand-in: every vote cites the fixture's literal quotes and finds a material error."""

    def __init__(self, fixture_input: dict[str, Any]):
        self._quotes = list(fixture_input["judge_quotes"])

    def request_vote(self, *, model: str, vote_slot: int, attempt_number: int) -> dict[str, Any]:
        shown = "; ".join(f'"{quote}"' for quote in self._quotes)
        return {
            "evidence_sufficient": True,
            "material_error_present": True,
            "evidence_quotes": list(self._quotes),
            "explanation": f"The cited source states {shown}. That does not support the sentence as written.",
        }


def simulate_pairs(sentences: list[dict[str, Any]], reference_map: dict[int, dict[str, Any]]) -> list[PairResult]:
    """One simulated pair per cited (sentence, reference); the first ones take the spec fixtures as unsupported."""
    fixtures = [_load_fixture(name) for name in FIXTURE_NAMES]
    used = 0
    pairs: list[PairResult] = []
    for sentence in sentences:
        text = sentence["text"]
        sentence_id = sentence["id"]
        numbers = parse_citation_markers(text) or resolve_reference_numbers_for_sentence(text, reference_map)
        if not numbers:
            pairs.append(PairResult(sentence_id, text, status="no_citations_found"))
            continue
        context = "sole" if len(numbers) == 1 else "one_of_several"
        for number in numbers:
            entry = reference_map.get(number)
            if entry is None:
                pairs.append(PairResult(sentence_id, text, number, status="reference_not_in_list"))
                continue
            doi = entry.get("doi")
            if used >= len(fixtures):
                pairs.append(PairResult(
                    sentence_id, text, number, doi, status="verdict", source_type="abstract",
                    citation_context=context, verdict="uncertain",
                ))
                continue
            fixture_input = fixtures[used]["input"]
            used += 1
            judge = run_hybrid_luna_sol_cascade(
                claim=text,
                evidence=fixture_input["evidence"],
                source_type=fixture_input["source_type"],
                citation_context=context,
                case_id=f"SIM-{sentence_id}-{number}",
                provider=_FixtureVoteProvider(fixture_input),
            )
            quotes, explanations = unsupported_sol_votes_from_judge_payload(judge)
            verdict = judge["final_verdict"]
            pairs.append(PairResult(
                sentence_id, text, number, doi, status="verdict",
                source_type=fixture_input["source_type"], citation_context=context, verdict=verdict,
                explanation=judge["explanation"],
                evidence_quotes=list(quotes),
                evidence_blocks=list(fixture_input["evidence"]),
                judge_quotes=quotes,
                judge_explanations=explanations,
                show_content=verdict == "unsupported",
            ))
    return pairs
