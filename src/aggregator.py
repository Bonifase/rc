"""
Groups (sentence, source) pair results back into one card per sentence.

Per the integration note:
- "Store one result per (sentence, source) pair" -- that's PairResult, from pipeline.py.
- "Group them by sentence for display" -- one card per sentence, all its
  sources' passages shown inside it.
- "The sentence-level verdict is a FOUR-LEVEL aggregation, worst to best":
  contradiction > no comparable evidence > some uncertain / not comparable >
  everything supports. This is deliberately NOT a simple worst-of-the-verdicts:
  non-verdict pairs (sources that could not be compared) move a sentence DOWN
  from "everything supports" to "some uncertain / not comparable" instead of
  being ignored.
- Two counts that are NOT interchangeable: sentences vs. checks (pairs).
"""
from dataclasses import asdict, dataclass
from typing import Optional

from src.pipeline import PairResult

# Four-level sentence aggregation, worst -> best:
#   1 = contradiction           -- at least one pair contradicts the claim
#   2 = no comparable evidence  -- no pair reached a content verdict at all
#   3 = some uncertain / not comparable
#                              -- mixed pairs: uncertain verdicts or sources
#                                 that could not be compared alongside real
#                                 verdicts
#   4 = everything supports     -- every pair reached "supported"
_LEVEL_TO_VERDICT = {1: "unsupported", 2: None, 3: "uncertain", 4: "supported"}


def _aggregation_level(pairs: list[PairResult]) -> int:
    """Assigns the four-level sentence outcome.

    Guard 2 still holds: a non-verdict pair status is never folded into a
    content verdict as if it were one. But it is NOT ignored either -- a
    supported source next to an unreadable source is "some uncertain / not
    comparable" (level 3), never "everything supports" (level 4).
    """
    verdict_pairs = [p for p in pairs if p.status == "verdict"]
    if any(p.verdict == "unsupported" for p in verdict_pairs):
        return 1
    if not verdict_pairs:
        return 2
    if any(p.verdict == "uncertain" for p in verdict_pairs):
        return 3
    if any(p.status != "verdict" for p in pairs):
        return 3
    return 4


@dataclass
class SentenceRecord:
    sentence_id: str
    sentence_text: str
    verdict: Optional[str]          # four-level verdict, or None for "no comparable evidence"
    has_incomplete_pairs: bool       # True if any pair didn't reach a clean content verdict
    aggregation_level: int           # 1 = contradiction, 2 = no comparable evidence,
                                     # 3 = some uncertain / not comparable, 4 = everything supports
    pairs: list[PairResult]


def build_sentence_records(pair_results: list[PairResult]) -> list[SentenceRecord]:
    """
    Groups pairs by sentence_id, preserving the order sentences first
    appeared in. A sentence with zero citation markers still gets exactly
    one PairResult (status="no_citations_found") from pipeline.py, so every
    sentence produces a card even when there was nothing to check.
    """
    order: list[str] = []
    grouped: dict[str, list[PairResult]] = {}

    for pair in pair_results:
        if pair.sentence_id not in grouped:
            grouped[pair.sentence_id] = []
            order.append(pair.sentence_id)
        grouped[pair.sentence_id].append(pair)

    records = []
    for sentence_id in order:
        pairs = grouped[sentence_id]
        level = _aggregation_level(pairs)
        records.append(SentenceRecord(
            sentence_id=sentence_id,
            sentence_text=pairs[0].sentence_text,
            verdict=_LEVEL_TO_VERDICT[level],
            has_incomplete_pairs=any(p.status != "verdict" for p in pairs),
            aggregation_level=level,
            pairs=pairs,
        ))
    return records


def build_document_summary(pair_results: list[PairResult], sentence_records: list[SentenceRecord]) -> dict:
    """
    Two distinct counts, kept separate per the integration note:
    - total_sentences: how many sentence cards there are
    - total_checks: how many actual MODEL CALLS were made -- per the
      integration note, "checks is the number of model calls," distinct
      from the number of (sentence, source) pairs attempted. A pair that
      failed before ever reaching the LLM (DOI didn't resolve, source not
      indexed, no usable content) is a real pair but not a check.

    sentence_verdict_counts groups by whatever verdict string is present,
    with "no comparable evidence" (the four-level label) for sentences where
    no pair reached a real verdict -- generic by design, same reasoning as
    the original aggregator: no hardcoded taxonomy to maintain here.
    """
    total_checks = sum(1 for p in pair_results if p.llm_call_attempted)

    sentence_verdict_counts: dict[str, int] = {}
    for record in sentence_records:
        key = record.verdict or "no comparable evidence"
        sentence_verdict_counts[key] = sentence_verdict_counts.get(key, 0) + 1

    # Separately surface non-verdict pair outcomes (technical failures,
    # not-indexed, etc.) for debugging/reporting -- distinct from verdict
    # counts, per Guard 2: these must never be silently folded into a
    # content verdict.
    pair_status_counts: dict[str, int] = {}
    for pair in pair_results:
        if pair.status == "no_citations_found":
            continue
        pair_status_counts[pair.status] = pair_status_counts.get(pair.status, 0) + 1

    return {
        "total_sentences": len(sentence_records),
        "total_checks": total_checks,
        "sentence_verdict_counts": sentence_verdict_counts,
        "pair_status_counts": pair_status_counts,
    }


def generate_report(pair_results: list[PairResult]) -> dict:
    sentence_records = build_sentence_records(pair_results)
    return {
        "lang": "en",
        "report_language": "en",
        "sentences": [
            {
                "sentence_id": r.sentence_id,
                "sentence_text": r.sentence_text,
                "verdict": r.verdict,
                "aggregation_level": r.aggregation_level,
                "has_incomplete_pairs": r.has_incomplete_pairs,
                "pairs": [asdict(p) for p in r.pairs],
            }
            for r in sentence_records
        ],
        "summary": build_document_summary(pair_results, sentence_records),
    }
