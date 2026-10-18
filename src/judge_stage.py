"""Shared rules for the judge-stage contract used by T-10 through T-14.

This module intentionally avoids any paid API dependency and stays in the
verification layer only. It mirrors the required behavior described in the task
matrix:

- evidence is extracted once per case and reused across both judge models
- Luna and Sol each receive six votes in the standard judge flow
- invalid ballots are ignored and retried once instead of counting as
  unsupported
- a supported majority of 4-of-6 wins the Sol decision; otherwise the result is
  uncertain and may be flagged
- disagreement between the model-level majorities is a flag condition
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Iterable, Sequence

VALID_VERDICTS = {"supported", "unsupported", "uncertain"}
_VALID_JUDGE_VOTE_FIELDS = {
    "evidence_sufficient",
    "material_error_present",
    "evidence_quotes",
    "explanation",
}


def normalize_verdict(value: object) -> str | None:
    """Return a canonical verdict or None when the ballot is invalid."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in VALID_VERDICTS:
            return text
        if text in {"support", "supported_yes", "yes"}:
            return "supported"
        if text in {"reject", "unsupported_no", "no"}:
            return "unsupported"
        if text in {"uncertain", "unknown", "not_sure", "ambiguous"}:
            return "uncertain"
    return None


def clean_votes(votes: Iterable[object]) -> list[str]:
    return [v for v in (normalize_verdict(v) for v in votes) if v is not None]


def counts_for(votes: Sequence[str]) -> dict[str, int]:
    counter = Counter(votes)
    return {
        "supported": int(counter.get("supported", 0)),
        "unsupported": int(counter.get("unsupported", 0)),
        "uncertain": int(counter.get("uncertain", 0)),
    }


def majority_verdict(votes: Sequence[str], threshold: int = 4) -> dict[str, object]:
    """Return the decisive majority verdict for a vote set.

    A threshold of 4-of-6 is required for a valid decision. If no verdict reaches
    the threshold, the case is treated as uncertain and flagged.
    """
    cleaned = clean_votes(votes)
    if not cleaned:
        return {"verdict": "uncertain", "threshold_met": False, "flagged": True, "counts": {"supported": 0, "unsupported": 0, "uncertain": 0}}

    counter = counts_for(cleaned)
    for verdict in ("supported", "unsupported", "uncertain"):
        if counter[verdict] >= threshold:
            return {"verdict": verdict, "threshold_met": True, "flagged": False, "counts": counter}

    return {"verdict": "uncertain", "threshold_met": False, "flagged": True, "counts": counter}


def resolve_case_decision(luna_votes: Sequence[object], sol_votes: Sequence[object]) -> dict[str, object]:
    """Apply the judge cascade described by the task matrix.

    Rules implemented:
    - Luna gets six votes; all six supported => supported, no escalation
    - anything else triggers Sol's six votes
    - Sol requires a 4-of-6 majority to decide; otherwise the case is uncertain
    - if the model majorities disagree, the case is flagged
    """
    luna_clean = clean_votes(luna_votes)
    sol_clean = clean_votes(sol_votes)
    luna_majority = majority_verdict(luna_clean, threshold=4)
    if len(luna_clean) >= 6 and luna_clean.count("supported") == 6:
        return {
            "final_verdict": "supported",
            "flagged": False,
            "escalated": False,
            "luna_counts": counts_for(luna_clean),
            "sol_counts": counts_for(sol_clean),
            "luna_majority": "supported",
            "sol_majority": None,
        }

    if not sol_clean:
        return {
            "final_verdict": "uncertain",
            "flagged": True,
            "escalated": True,
            "luna_counts": counts_for(luna_clean),
            "sol_counts": counts_for(sol_clean),
            "luna_majority": luna_majority["verdict"],
            "sol_majority": None,
        }

    sol_majority = majority_verdict(sol_clean, threshold=4)
    final_verdict = sol_majority["verdict"]
    disagreement = luna_majority["verdict"] != sol_majority["verdict"]
    flagged = bool(sol_majority.get("flagged") or disagreement)
    return {
        "final_verdict": final_verdict,
        "flagged": flagged,
        "escalated": True,
        "luna_counts": counts_for(luna_clean),
        "sol_counts": counts_for(sol_clean),
        "luna_majority": luna_majority["verdict"],
        "sol_majority": sol_majority["verdict"],
    }


def _normalize_line_endings(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _quote_is_exact_literal_in_evidence(quote: str, evidence: Sequence[dict[str, object]] | None) -> bool:
    if not isinstance(quote, str):
        return False
    normalized_quote = _normalize_line_endings(quote)
    if evidence is None:
        return True
    for block in evidence:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str) and normalized_quote in _normalize_line_endings(text):
            return True
    return False


def _reply_has_exact_vote_contract(raw_reply: object, evidence: Sequence[dict[str, object]] | None = None) -> bool:
    if raw_reply is None:
        return False
    if isinstance(raw_reply, dict):
        payload = raw_reply
    else:
        try:
            payload = json.loads(str(raw_reply))
        except (TypeError, ValueError):
            return False
    if not isinstance(payload, dict):
        return False
    if set(payload.keys()) != _VALID_JUDGE_VOTE_FIELDS:
        return False
    if type(payload.get("evidence_sufficient")) is not bool:
        return False
    if type(payload.get("material_error_present")) is not bool:
        return False
    if not isinstance(payload.get("evidence_quotes"), list):
        return False
    if any(type(quote) is not str for quote in payload["evidence_quotes"]):
        return False
    if type(payload.get("explanation")) is not str:
        return False
    if evidence is not None:
        try:
            for quote in payload["evidence_quotes"]:
                if not quote.strip():
                    return False
                if not _quote_is_exact_literal_in_evidence(quote, evidence):
                    return False
        except Exception:
            return False
    return True


def build_call_log_entry(*, case_id: str, model: str, stage: str, call_index: int, verdict: str | None, raw_reply: str, prompt_tokens: int, completion_tokens: int, cost_usd: float, paid: bool = False, evidence: Sequence[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "case_id": case_id,
        "model": model,
        "stage": stage,
        "call_index": int(call_index),
        "call_key": f"{case_id}::{model}::{stage}::{call_index}",
        "verdict": verdict,
        "raw_reply": raw_reply,
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "cost_usd": float(cost_usd),
        "paid": bool(paid),
        "valid": _reply_has_exact_vote_contract(raw_reply, evidence=evidence),
    }


def normalize_call_log(entries: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("raw_reply") or ""
        verdict = normalize_verdict(entry.get("verdict"))
        normalized.append({
            "case_id": entry.get("case_id"),
            "model": entry.get("model"),
            "stage": entry.get("stage"),
            "call_index": int(entry.get("call_index", 0) or 0),
            "call_key": entry.get("call_key") or f"{entry.get('case_id')}::{entry.get('model')}::{entry.get('stage')}::{entry.get('call_index',0)}",
            "verdict": verdict,
            "raw_reply": str(raw),
            "prompt_tokens": int(entry.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(entry.get("completion_tokens", 0) or 0),
            "cost_usd": float(entry.get("cost_usd", 0.0) or 0.0),
            "paid": bool(entry.get("paid", False)),
            "valid": _reply_has_exact_vote_contract(raw),
        })
    return normalized


__all__ = [
    "VALID_VERDICTS",
    "normalize_verdict",
    "clean_votes",
    "counts_for",
    "majority_verdict",
    "resolve_case_decision",
    "build_call_log_entry",
    "normalize_call_log",
]
