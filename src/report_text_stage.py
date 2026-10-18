"""Report-text stage for unsupported citation cases.

It runs only after a final verdict has already been produced, and it never feeds
its output back into that verdict logic. The provider is always explicit: the real
Luna model in live mode, canned text only under the offline-simulation opt-in.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
_PROMPT_MISSING = "Report-text prompt not available in the local offline bundle."

REPORT_TEXT_MODEL_SETTINGS = {
    "model": "gpt-5.6-luna",
    "api": "chat_completions",
    "timeout_seconds": 60,
    "max_completion_tokens": 8000,
    "max_retries": 0,
    "temperature": None,
    "seed": None,
    "reasoning": None,
    "response_format": None,
}


def _candidate_prompt_paths() -> list[Path]:
    return [
        ROOT / "prompts" / "report_text_v2.txt",
        ROOT / "src" / "prompts" / "report_text_v2.txt",
        ROOT / "REPORT_STAGE_FINAL_WITH_METADATA" / "prompts" / "report_text_v2.txt",
    ]


def load_report_text_prompt(prompt_path: str | Path | None = None) -> str:
    candidates: list[Path] = []
    if prompt_path is not None:
        candidates.append(Path(prompt_path))
    candidates.extend(_candidate_prompt_paths())
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_bytes().decode("utf-8")  # byte for byte, no newline translation
    return _PROMPT_MISSING


def _default_error_types() -> list[dict[str, str]]:
    fixture_candidates = [
        ROOT / "fixtures" / "content_error_types_en.json",
        ROOT / "REPORT_STAGE_FINAL_WITH_METADATA" / "fixtures" / "content_error_types_en.json",
    ]
    for fixture_path in fixture_candidates:
        if fixture_path.exists():
            with fixture_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
    return [
        {"key": "source_says_opposite", "label": "The source says the opposite", "description": "The cited work says the opposite."},
        {"key": "study_did_not_do_that", "label": "The study did not do that", "description": "The study did not have the described design or scope."},
        {"key": "data_mismatch", "label": "Data does not match the study", "description": "The study reports a different entity."},
        {"key": "inflated_figure", "label": "Inflated figure", "description": "The reported figure is larger than the source."},
        {"key": "figure_out_of_context", "label": "Figure taken out of context", "description": "The figure is used for a different context."},
        {"key": "measure_confused", "label": "One measure confused with another", "description": "The measure in the sentence is not the one in the source."},
        {"key": "source_does_not_say", "label": "The source does not say that", "description": "The source does not report this claim."},
        {"key": "overstated_certainty", "label": "Overstated certainty", "description": "The sentence treats a suggestion as a fact."},
    ]


def build_report_text_input(
    *,
    claim: str,
    marker: str,
    evidence: list[dict[str, Any]],
    source_type: str,
    citation_context: str,
    judge_quotes: Iterable[str],
    judge_explanations: Iterable[str],
    error_types: list[dict[str, str]] | None = None,
    report_language: str = "en",
) -> dict[str, Any]:
    return {
        "report_language": report_language,
        "citation_context": citation_context,
        "marker": marker,
        "claim": claim,
        "source_type": source_type,
        "evidence": evidence,
        "judge_quotes": list(judge_quotes),
        "judge_explanations": list(judge_explanations),
        "error_types": error_types or _default_error_types(),
    }


def collect_unsupported_sol_votes(votes: Iterable[dict[str, Any]]) -> tuple[list[str], list[str]]:
    quotes: list[str] = []
    explanations: list[str] = []
    seen_quotes: set[str] = set()
    seen_explanations: set[str] = set()

    for vote in votes:
        if not isinstance(vote, dict):
            continue
        response = vote.get("response") if isinstance(vote.get("response"), dict) else vote
        vote_status = str(vote.get("status", "")).strip().lower()
        if vote_status and vote_status != "unsupported":
            continue
        quote_items = response.get("evidence_quotes") if isinstance(response, dict) else []
        if not isinstance(quote_items, list):
            quote_items = []
        single_quote = response.get("evidence_quote") if isinstance(response, dict) else None
        if isinstance(single_quote, str):
            quote_items = [*quote_items, single_quote]
        for item in quote_items:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if not value or value in seen_quotes:
                continue
            seen_quotes.add(value)
            quotes.append(value)

        explanation = response.get("explanation") if isinstance(response, dict) else None
        if isinstance(explanation, str):
            value = explanation.strip()
            if value and value not in seen_explanations:
                seen_explanations.add(value)
                explanations.append(value)

    return quotes, explanations


def unsupported_sol_votes_from_judge_payload(payload: Any) -> tuple[list[str], list[str]]:
    """Quotes and explanations of the valid Sol votes that voted unsupported, in vote order."""
    if not isinstance(payload, dict):
        return [], []
    votes: list[dict[str, Any]] = []
    seen_slots: set[Any] = set()
    for index, entry in enumerate(payload.get("call_log") or []):
        if not isinstance(entry, dict) or str(entry.get("model", "")).lower() != "gpt-5.6-sol":
            continue
        if entry.get("validation_status") == "failed" or entry.get("verdict") != "unsupported":
            continue
        slot = entry.get("vote_slot", f"index-{index}")
        if slot in seen_slots:
            continue
        response = entry.get("vote_response")
        if not isinstance(response, dict):
            try:
                response = json.loads(entry.get("raw_reply") or "")
            except (TypeError, ValueError):
                continue
        if not isinstance(response, dict):
            continue
        seen_slots.add(slot)
        votes.append({"status": "unsupported", "response": response})
    return collect_unsupported_sol_votes(votes)


def final_vote_quotes(payload: Any) -> list[str]:
    """Judge-returned evidence quotes of the valid votes that match the final verdict.

    Sol votes are preferred when Sol decided; otherwise the Luna votes are used.
    """
    if not isinstance(payload, dict):
        return []
    verdict = payload.get("final_verdict")
    entries: list[dict[str, Any]] = []
    for entry in payload.get("call_log") or []:
        if not isinstance(entry, dict) or entry.get("validation_status") == "failed":
            continue
        if verdict and entry.get("verdict") != verdict:
            continue
        entries.append(entry)
    sol = [e for e in entries if str(e.get("model", "")).lower() == "gpt-5.6-sol"]
    quotes: list[str] = []
    for entry in sol or entries:
        response = entry.get("vote_response")
        if not isinstance(response, dict):
            try:
                response = json.loads(entry.get("raw_reply") or "")
            except (TypeError, ValueError):
                continue
        items = response.get("evidence_quotes") if isinstance(response, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str) and item.strip() and item.strip() not in quotes:
                quotes.append(item.strip())
    return quotes


def sol_not_established_votes(payload: Any) -> tuple[int, int]:
    """(valid Sol votes with evidence_sufficient=false and material_error_present=false, valid Sol votes)."""
    if not isinstance(payload, dict):
        return 0, 0
    by_slot: dict[Any, dict[str, Any]] = {}
    for index, entry in enumerate(payload.get("call_log") or []):
        if not isinstance(entry, dict) or str(entry.get("model", "")).lower() != "gpt-5.6-sol":
            continue
        if entry.get("validation_status") == "failed":
            continue
        response = entry.get("vote_response")
        if not isinstance(response, dict):
            try:
                response = json.loads(entry.get("raw_reply") or "")
            except (TypeError, ValueError):
                continue
        if isinstance(response, dict):
            by_slot[entry.get("vote_slot", f"index-{index}")] = response
    not_established = sum(1 for r in by_slot.values() if r.get("evidence_sufficient") is False and r.get("material_error_present") is False)
    return not_established, len(by_slot)


def _mock_report_text_reply(report_input: dict[str, Any]) -> dict[str, str | None]:
    claim = str(report_input["claim"])
    evidence_text = "\n".join(str(item.get("text", "")) for item in report_input.get("evidence", []))
    evidence_text_clean = evidence_text.strip()
    selected_type = next((item["key"] for item in report_input["error_types"] if item["key"] == "source_does_not_say"), "source_does_not_say")
    claim_marked = f"**{claim}**" if claim else "**the claim**"
    evidence_passage = f"**{evidence_text_clean[:240]}**" if evidence_text_clean else "**the supplied source excerpt**"
    evidence_context = None if report_input["source_type"] == "full_text" else "This study reports the relevant experimental result and uses a design consistent with the evidence provided."
    observation = "The source does not report the sentence's central claim. The evidence supports a narrower or different finding, so the manuscript statement overstates what the cited work demonstrates."
    return {
        "error_type": selected_type,
        "claim_marked": claim_marked,
        "evidence_passage": evidence_passage,
        "evidence_context": evidence_context,
        "observation": observation,
    }


def _report_text_validator():
    validator_path = ROOT / "reference" / "validate_report_text.py"
    if not validator_path.exists():
        return None
    import importlib.util

    spec = importlib.util.spec_from_file_location("ref_validate_report_text", validator_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "validate", None)


def _audit_entry(*, case_id: str | None, attempt_number: int, prompt_text: str, report_input: dict[str, Any], response: dict[str, Any] | None, status: str, call_key: str, model: str = "gpt-5.6-luna", usage: dict[str, int] | None = None, paid: bool = False) -> dict[str, Any]:
    metrics = usage if usage else ((response or {}).get("usage") if isinstance(response, dict) else None)
    prompt_tokens = int((metrics or {}).get("prompt_tokens", 0) or 0) if isinstance(metrics, dict) else 0
    completion_tokens = int((metrics or {}).get("completion_tokens", 0) or 0) if isinstance(metrics, dict) else 0
    cached_tokens = int(((metrics or {}).get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0) if isinstance(metrics, dict) else 0
    input_hash = hashlib.sha256(json.dumps(report_input, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    return {
        "stage": "report_text",
        "case_id": case_id,
        "call_key": call_key,
        "model": model,
        "paid": paid,
        "prompt_hash": prompt_hash,
        "input_hash": input_hash,
        "attempt_number": int(attempt_number),
        "status": status,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "cost_usd": 0.0,
    }


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


class OpenAIReportTextProvider:
    """One real Chat Completions request per attempt with the section 2 settings of the spec."""

    _FATAL_STATUS_CODES = {400, 401, 403, 404}

    def __init__(self, client: Any | None = None):
        self._client = client
        self.last_usage: dict[str, int] = {}
        self.last_cached = False

    def __call__(self, *, report_input: dict[str, Any], prompt_text: str, call_key: str, attempt_number: int, model: str, request_config: dict[str, Any]) -> dict[str, Any]:
        from src.model_call_store import FatalModelCallError, cached_call

        if prompt_text == _PROMPT_MISSING:
            raise FatalModelCallError("prompts/report_text_v2.txt was not found; refusing to call the model without the approved prompt.")
        user_message = json.dumps(report_input, indent=2, ensure_ascii=False)

        def send() -> tuple[str, dict[str, int]]:
            if self._client is None:
                from src.llm_wrapper import _get_client

                self._client = _get_client()
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": prompt_text},
                        {"role": "user", "content": user_message},
                    ],
                    timeout=request_config["timeout_seconds"],
                    max_completion_tokens=request_config["max_completion_tokens"],
                )
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status in self._FATAL_STATUS_CODES:
                    raise FatalModelCallError(f"OpenAI rejected the report-text request (HTTP {status}): {exc}") from exc
                raise
            usage = getattr(response, "usage", None)
            return (response.choices[0].message.content or ""), {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            }

        raw, self.last_usage, self.last_cached = cached_call(model=model, system=prompt_text, user=user_message, slot=call_key, send=send)
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return json.loads(text)


def _resolve_provider(provider: Any | None) -> tuple[Any | None, bool]:
    """Return (provider, simulated). Canned text is produced only under the explicit simulation opt-in."""
    if provider is not None and callable(provider):
        return provider, False
    from src.run_mode import run_mode

    mode = run_mode()  # raises when live and simulation are both enabled
    if mode == "simulation":
        return None, True
    if mode == "live":
        from src import config

        key = (config.OPENAI_API_KEY or "").strip().lower()
        if key and key not in {"offline", "mock", "disabled"}:
            return OpenAIReportTextProvider(), False
        raise RuntimeError("OPENAI_API_KEY is not set; the report-text stage cannot call the model.")
    raise RuntimeError(
        "No report-text provider: set REFCHECK_LIVE_JUDGE=1 with OPENAI_API_KEY to call the model, "
        "or REFCHECK_ALLOW_OFFLINE_SIMULATION=1 for simulated text."
    )


def request_report_text(
    report_input: dict[str, Any],
    *,
    prompt_path: str | Path | None = None,
    provider: Any | None = None,
    case_id: str | None = None,
    audit_ledger: list[dict[str, Any]] | None = None,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Send the report-text request, with one retry on an invalid reply and an audit row per attempt."""
    prompt_text = load_report_text_prompt(prompt_path)
    ledger = audit_ledger if audit_ledger is not None else []
    resolved_case_id = str(case_id or report_input.get("marker") or "report_text_case")
    validator = _report_text_validator()
    provider, simulated = _resolve_provider(provider)
    paid = isinstance(provider, OpenAIReportTextProvider)
    from src.model_call_store import FatalModelCallError

    for attempt in range(1, max_attempts + 1):
        call_key = f"{resolved_case_id}::gpt-5.6-luna::report_text::{attempt}"
        try:
            if provider is not None:
                reply = provider(report_input=report_input, prompt_text=prompt_text, call_key=call_key, attempt_number=attempt, model="gpt-5.6-luna", request_config=REPORT_TEXT_MODEL_SETTINGS)
            else:
                reply = _mock_report_text_reply(report_input)
            if not isinstance(reply, dict):
                raise TypeError("Provider response was not a JSON object.")
            errors = validator(report_input, reply) if validator is not None else []
            ledger.append(_audit_entry(case_id=resolved_case_id, attempt_number=attempt, prompt_text=prompt_text, report_input=report_input, response=reply, status="invalid" if errors else "ok", call_key=call_key, usage=getattr(provider, "last_usage", None), paid=paid and not getattr(provider, "last_cached", False)))
            if errors:
                ledger[-1]["validation_errors"] = list(errors)
                if attempt < max_attempts:
                    continue
                return {"status": "report_text_failed", "reply": reply, "errors": errors, "attempts": attempt, "audit_ledger": ledger, "call_key": call_key, "simulated": simulated}
            return {"status": "ready", "reply": reply, "attempts": attempt, "audit_ledger": ledger, "call_key": call_key, "simulated": simulated}
        except FatalModelCallError:
            raise
        except Exception as exc:
            ledger.append(_audit_entry(case_id=resolved_case_id, attempt_number=attempt, prompt_text=prompt_text, report_input=report_input, response=None, status="transport_error", call_key=call_key, usage=getattr(provider, "last_usage", None), paid=paid))
            ledger[-1]["error_message"] = str(exc)
            if attempt < max_attempts:
                continue
            return {"status": "report_text_failed", "reply": {}, "errors": [str(exc)], "attempts": attempt, "audit_ledger": ledger, "call_key": call_key, "simulated": simulated}

    return {"status": "report_text_failed", "reply": {}, "errors": ["report-text stage exhausted retries"], "attempts": max_attempts, "audit_ledger": ledger, "call_key": call_key, "simulated": simulated}


def maybe_run_report_text_stage(
    *,
    claim: str,
    marker: str,
    evidence: list[dict[str, Any]],
    source_type: str,
    citation_context: str,
    final_verdict: str,
    judge_quotes: list[str] | None = None,
    judge_explanations: list[str] | None = None,
    show_content: bool = True,
    prompt_path: str | Path | None = None,
    provider: Any | None = None,
    case_id: str | None = None,
    audit_ledger: list[dict[str, Any]] | None = None,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Run the report-text stage only after a final unsupported verdict."""
    if final_verdict != "unsupported":
        return {
            "status": "skipped",
            "report_complete": False,
            "verdict_preserved": final_verdict,
            "reason": "report text runs only for unsupported verdicts",
        }
    if not show_content:
        return {
            "status": "skipped",
            "report_complete": False,
            "verdict_preserved": final_verdict,
            "reason": "show_content is false for this reference",
        }

    report_input = build_report_text_input(
        claim=claim,
        marker=marker,
        evidence=evidence,
        source_type=source_type,
        citation_context=citation_context,
        judge_quotes=judge_quotes or [],
        judge_explanations=judge_explanations or [],
    )
    prompt_text = load_report_text_prompt(prompt_path)
    result = request_report_text(report_input, prompt_path=prompt_path, provider=provider, case_id=case_id, audit_ledger=audit_ledger, max_attempts=max_attempts)
    raw_reply = result.get("reply", result)
    validator = _report_text_validator()
    errors = validator(report_input, raw_reply) if validator is not None and isinstance(raw_reply, dict) else []

    if result.get("status") == "report_text_failed" or errors:
        return {
            "status": "report_text_failed",
            "report_complete": False,
            "verdict_preserved": final_verdict,
            "reason": "report text validation failed",
            "input": report_input,
            "reply": raw_reply,
            "errors": errors or result.get("errors", []),
            "prompt_path": str(prompt_path) if prompt_path else str(_candidate_prompt_paths()[0]),
            "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
            "attempts": result.get("attempts", 0),
            "audit_ledger": result.get("audit_ledger", []),
            "call_key": result.get("call_key"),
            "simulated": result.get("simulated", False),
        }

    return {
        "status": "ready",
        "report_complete": True,
        "verdict_preserved": final_verdict,
        "input": report_input,
        "reply": raw_reply,
        "prompt_path": str(prompt_path) if prompt_path else str(_candidate_prompt_paths()[0]),
        "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
        "attempts": result.get("attempts", 1),
        "audit_ledger": result.get("audit_ledger", []),
        "call_key": result.get("call_key"),
        "simulated": result.get("simulated", False),
    }


__all__ = [
    "OpenAIReportTextProvider",
    "REPORT_TEXT_MODEL_SETTINGS",
    "build_report_text_input",
    "collect_unsupported_sol_votes",
    "load_report_text_prompt",
    "maybe_run_report_text_stage",
    "request_report_text",
    "unsupported_sol_votes_from_judge_payload",
]
