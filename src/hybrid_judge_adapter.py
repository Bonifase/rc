"""Offline hybrid judge adapter for the v2.2.2 PaperPal integration.

This keeps the production runtime separate from the verification layer while
providing a deterministic mock Luna→Sol cascade for the judge path without any
paid API calls. The adapter intentionally exposes the exact JSON structure the
v2.2.2 prompts expect, and it records a full call ledger like the production
judge should.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
from typing import Any

from src.config import OPENAI_API_KEY
from src.judge_stage import resolve_case_decision
from src.model_call_store import FatalModelCallError, cached_call


def _selected_hybrid_version() -> str:
    raw = os.getenv("REFCHECK_HYBRID_JUDGE_VERSION", "v2.2.2").strip().lower()
    if raw in {"v2.1", "2.1", "v2_1"}:
        return "v2.1"
    if raw in {"v2.2.2", "2.2.2", "v222", "v2_2_2"}:
        return "v2.2.2"
    return "v2.2.2"


def _resolve_case_id_for_pair(claim: str, evidence: list[dict[str, Any]], case_id: str | None = None) -> str:
    proposed = (case_id or "").strip()
    if proposed and proposed != "PP01-CASE":
        return proposed
    digest_seed = "\n".join(str(item.get("text", "")) for item in evidence if isinstance(item, dict))
    digest = hashlib.sha1((str(claim or "") + "\n" + digest_seed).encode("utf-8")).hexdigest()[:8]
    return f"PP01-CASE-{digest}"


def _candidate_prompt_locations() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    return [
        root / "judge_prompts" / "judge_hybrid_sole_v2_2_2.txt",
        root / "judge_prompts" / "judge_hybrid_multicita_v2_2_2.txt",
        root / "judge_prompts" / "judge_hybrid_sole_v2_1.txt",
        root / "judge_prompts" / "judge_hybrid_multicita_v2_1.txt",
        root / "REPORT_STAGE_FINAL_WITH_METADATA" / "judge_prompts" / "judge_hybrid_sole_v2_2_2.txt",
        root / "REPORT_STAGE_FINAL_WITH_METADATA" / "judge_prompts" / "judge_hybrid_multicita_v2_2_2.txt",
        root / "REPORT_STAGE_FINAL_WITH_METADATA" / "judge_prompts" / "judge_hybrid_sole_v2_1.txt",
        root / "REPORT_STAGE_FINAL_WITH_METADATA" / "judge_prompts" / "judge_hybrid_multicita_v2_1.txt",
        root / "prompts" / "judge_hybrid_sole_v2_2_2.txt",
        root / "prompts" / "judge_hybrid_multicita_v2_2_2.txt",
        root / ".." / "prompts" / "judge_hybrid_sole_v2_2_2.txt",
        root / ".." / "prompts" / "judge_hybrid_multicita_v2_2_2.txt",
    ]


def load_hybrid_prompt(citation_context: str, *, prompt_root: str | Path | None = None, version: str | None = None) -> str:
    """Load the matching configured hybrid judge prompt without enabling live calls."""
    normalized = citation_context.strip().lower()
    selected_version = _selected_hybrid_version() if version is None else str(version).strip().lower()
    selected_version = "v2.1" if selected_version in {"v2.1", "2.1", "v2_1"} else "v2.2.2"
    prompt_name = (
        f"judge_hybrid_sole_{selected_version.replace('.', '_')}.txt"
        if normalized == "sole"
        else f"judge_hybrid_multicita_{selected_version.replace('.', '_')}.txt"
    )
    search_roots: list[Path] = []
    if prompt_root is not None:
        search_roots.append(Path(prompt_root))
    search_roots.extend(_candidate_prompt_locations())

    for base in search_roots:
        candidate = base if base.name == prompt_name else base / prompt_name
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return prompt_name


_ALLOWED_CITATION_CONTEXTS = {"sole", "one_of_several"}
_ALLOWED_SOURCE_TYPES = {"abstract", "full_text"}
_ALLOWED_EVIDENCE_LOCATIONS = {"abstract", "body"}


def _coerce_evidence_entry(entry: Any) -> dict[str, str]:
    if not isinstance(entry, dict):
        raise ValueError("Each evidence item must be an object with exactly 'location' and 'text'.")
    if set(entry.keys()) != {"location", "text"}:
        raise ValueError("Each evidence item must contain exactly the fields 'location' and 'text'.")
    location = entry.get("location")
    text = entry.get("text")
    if location not in _ALLOWED_EVIDENCE_LOCATIONS:
        raise ValueError("Evidence location must be exactly 'abstract' or 'body'.")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Evidence text must be a non-empty string containing literal source text.")
    return {"location": location, "text": text}


def serialize_hybrid_judge_input(judge_input: dict[str, Any]) -> str:
    """Deterministically serialize a judge input using the exact frozen contract."""
    if not isinstance(judge_input, dict):
        raise ValueError("Hybrid judge input must be a dictionary.")
    return json.dumps(judge_input, indent=2, ensure_ascii=False)


_VALID_VOTE_RESPONSE_FIELDS = {
    "evidence_sufficient",
    "material_error_present",
    "evidence_quotes",
    "explanation",
}


def _normalize_line_endings(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _quote_is_exact_literal_in_evidence(quote: str, evidence: list[dict[str, Any]]) -> bool:
    if not isinstance(quote, str):
        return False
    normalized_quote = _normalize_line_endings(quote)
    for block in evidence:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str) and normalized_quote in _normalize_line_endings(text):
            return True
    return False


def validate_vote_response(vote_response: Any, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate a single judge vote against the exact four-field contract."""
    if not isinstance(vote_response, dict):
        raise ValueError("Vote response must be a JSON object.")
    if set(vote_response.keys()) != _VALID_VOTE_RESPONSE_FIELDS:
        raise ValueError("Vote response must contain exactly: evidence_sufficient, material_error_present, evidence_quotes, explanation.")

    evidence_sufficient = vote_response.get("evidence_sufficient")
    material_error_present = vote_response.get("material_error_present")
    if type(evidence_sufficient) is not bool:
        raise ValueError("evidence_sufficient must be a boolean.")
    if type(material_error_present) is not bool:
        raise ValueError("material_error_present must be a boolean.")
    if not evidence_sufficient and material_error_present:
        raise ValueError("Contradictory vote response: evidence_sufficient=false and material_error_present=true is forbidden.")

    evidence_quotes = vote_response.get("evidence_quotes")
    if not isinstance(evidence_quotes, list) or any(type(quote) is not str for quote in evidence_quotes):
        raise ValueError("evidence_quotes must be an array of strings.")
    if not evidence_quotes and not evidence_sufficient:
        raise ValueError("An uncertain vote must include at least one evidence quote describing the missing or partial evidence.")
    if evidence_sufficient and (not evidence_quotes or not any(quote.strip() for quote in evidence_quotes)):
        raise ValueError("A sufficient vote requires at least one non-empty literal quote.")
    for quote in evidence_quotes:
        if not quote:
            raise ValueError("Evidence quotes may not be empty strings.")
        if not _quote_is_exact_literal_in_evidence(quote, evidence):
            raise ValueError("Every evidence quote must be an exact literal substring of one supplied evidence text block.")

    explanation = vote_response.get("explanation")
    if type(explanation) is not str:
        raise ValueError("explanation must be a string.")

    return vote_response


def derive_vote_status(vote_response: dict[str, Any], evidence: list[dict[str, Any]] | None = None) -> str:
    """Map the validated four-field response to supported / unsupported / uncertain.

    The canonical prompt semantics are explicit here: when evidence is sufficient
    and a material incompatibility is present, the vote is unsupported; when the
    evidence is sufficient and no incompatibility is present, it is supported;
    otherwise the vote is uncertain.
    """
    resolved_evidence = evidence if evidence is not None else [{"text": ""}]
    validated = validate_vote_response(vote_response, resolved_evidence)
    if validated["evidence_sufficient"] is True and validated["material_error_present"] is True:
        return "unsupported"
    if validated["evidence_sufficient"] is True and validated["material_error_present"] is False:
        return "supported"
    return "uncertain"


def _is_non_literal_quote_error(exc: ValueError | str) -> bool:
    message = str(exc).lower()
    literal_markers = ("literal", "substring", "exact literal", "non-literal", "not a literal")
    return any(marker in message for marker in literal_markers)


def process_vote_response_with_retry(
    vote_response: Any,
    evidence: list[dict[str, Any]],
    *,
    retry_response: Any | None = None,
    case_id: str = "PP01-CASE",
    model: str = "gpt-5.6-luna",
    attempt_index: int = 1,
) -> dict[str, Any]:
    """Validate a vote and allow at most one retry only for a non-literal quote."""
    try:
        validated = validate_vote_response(vote_response, evidence)
        return {
            "vote_response": validated,
            "status": derive_vote_status(validated, evidence),
            "attempt_count": 1,
            "retry_used": False,
            "call_key": f"{case_id}::{model}::vote::{attempt_index}",
        }
    except ValueError as exc:
        if retry_response is None or not _is_non_literal_quote_error(exc):
            raise
        validated_retry = validate_vote_response(retry_response, evidence)
        return {
            "vote_response": validated_retry,
            "status": derive_vote_status(validated_retry, evidence),
            "attempt_count": 2,
            "retry_used": True,
            "call_key": f"{case_id}::{model}::vote::{attempt_index + 1}",
            "first_error": str(exc),
        }


def _is_internal_cascade_summary(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    compact = value.strip().lower()
    if not compact:
        return False
    markers = (
        "luna cleared",
        "sol did not reach",
        "sol reached at least four valid",
        "vote slots",
        "sol was not invoked",
        "final decision is supported",
        "final decision is unsupported",
        "final decision is uncertain",
        "four-vote majority",
    )
    return any(marker in compact for marker in markers)


def _vote_explanation(vote_entry: dict[str, Any] | None) -> str | None:
    if not isinstance(vote_entry, dict):
        return None
    response = vote_entry.get("response")
    if isinstance(response, dict):
        explanation = response.get("explanation")
        if isinstance(explanation, str) and explanation.strip() and not _is_internal_cascade_summary(explanation):
            return explanation.strip()
    explanation = vote_entry.get("explanation")
    if isinstance(explanation, str) and explanation.strip() and not _is_internal_cascade_summary(explanation):
        return explanation.strip()
    return None


def _pick_explanation(votes: list[dict[str, Any]], *, statuses: set[str] | None = None) -> str:
    for vote in votes:
        if not isinstance(vote, dict):
            continue
        if statuses is not None and vote.get("status") not in statuses:
            continue
        explanation = _vote_explanation(vote)
        if explanation:
            return explanation
    return ""


def _user_visible_default_explanation() -> str:
    return "The judge did not return a scientific explanation for this case."


def _pick_researcher_explanation(*candidates: Any) -> str:
    """Prefer the actual judge explanation over internal cascade summary strings."""
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        text = candidate.strip()
        if not text or _is_internal_cascade_summary(text):
            continue
        return text
    return _user_visible_default_explanation()


def _build_mock_vote_response(claim: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    claim_text = (claim or "").strip()
    if not evidence:
        return {
            "evidence_sufficient": False,
            "material_error_present": False,
            "evidence_quotes": [],
            "explanation": "No source evidence was provided for the claim.",
        }

    first_text = str(evidence[0].get("text", ""))
    lower_claim = claim_text.lower()
    lower_evidence = first_text.lower()
    positive_markers = ["reduced", "increased", "improved", "enhanced", "supports", "demonstrates", "led to"]
    contradiction_markers = ["did not", "no effect", "not observed", "opposite", "decreased instead"]

    if any(marker in lower_claim or marker in lower_evidence for marker in positive_markers):
        return {
            "evidence_sufficient": True,
            "material_error_present": False,
            "evidence_quotes": [first_text],
            "explanation": "The claim is supported by the supplied evidence and remains consistent with the source.",
        }
    if any(marker in lower_evidence for marker in contradiction_markers):
        return {
            "evidence_sufficient": True,
            "material_error_present": True,
            "evidence_quotes": [first_text],
            "explanation": "The source explicitly contradicts the claim's attribution, so the supplied evidence establishes a material error.",
        }
    return {
        "evidence_sufficient": False,
        "material_error_present": False,
        "evidence_quotes": [],
        "explanation": "The provided evidence neither confirms nor definitively contradicts the claim's attribution.",
    }


MODEL_REQUEST_SETTINGS = {
    "gpt-5.6-luna": {
        "timeout_seconds": 60,
        "max_completion_tokens": 8000,
        "sdk_max_retries": 0,
        "temperature": None,
        "seed": None,
        "reasoning": None,
        "response_format": None,
    },
    "gpt-5.6-sol": {
        "timeout_seconds": 300,
        "max_completion_tokens": 8000,
        "sdk_max_retries": 0,
        "temperature": None,
        "seed": None,
        "reasoning": None,
        "response_format": None,
    },
}


def _request_settings_for(model: str) -> dict[str, Any]:
    model_name = (model or "").strip().lower()
    settings = MODEL_REQUEST_SETTINGS.get(model_name, MODEL_REQUEST_SETTINGS["gpt-5.6-luna"]).copy()
    settings["model"] = model_name if model_name in MODEL_REQUEST_SETTINGS else "gpt-5.6-luna"
    return {key: value for key, value in settings.items() if value is not None and key not in {"temperature", "seed", "reasoning", "response_format"}}


def _call_provider_vote(
    provider: Any,
    *,
    model: str,
    vote_slot: int,
    attempt_number: int,
    judge_input: dict[str, Any],
    prompt_text: str,
    request_config: dict[str, Any],
) -> dict[str, Any]:
    if provider is None:
        return _build_mock_vote_response(judge_input["claim"], judge_input["evidence"])

    base_payload = {
        "model": model,
        "vote_slot": vote_slot,
        "attempt_number": attempt_number,
        "judge_input": judge_input,
        "prompt_text": prompt_text,
        "request_config": request_config,
    }

    if hasattr(provider, "request_vote"):
        try:
            signature = inspect.signature(provider.request_vote)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            allowed = set(signature.parameters)
            filtered = {key: val for key, val in base_payload.items() if key in allowed}
            return provider.request_vote(**filtered)
        return provider.request_vote(**base_payload)

    if hasattr(provider, "request"):
        try:
            signature = inspect.signature(provider.request)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            allowed = set(signature.parameters)
            filtered = {key: val for key, val in base_payload.items() if key in allowed}
            return provider.request(**filtered)
        return provider.request(**base_payload)

    raise TypeError("Provider must implement request_vote(...) or request(...).")


def _normalize_vote_status(response: dict[str, Any], evidence: list[dict[str, Any]]) -> tuple[str, str | None, str | None]:
    try:
        validated = validate_vote_response(response, evidence)
    except ValueError as exc:
        return "failed", "invalid_vote", str(exc)
    status = derive_vote_status(validated, evidence)
    return status, None, None


def _resolve_vote_outcome_for_threshold(votes: list[dict[str, Any]], *, evidence: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    supported = 0
    unsupported = 0
    uncertain = 0
    invalid = 0
    for vote in votes:
        status, failure_category, _ = _normalize_vote_status(vote.get("response", {}), evidence)
        if failure_category is not None:
            invalid += 1
            continue
        if status == "supported":
            supported += 1
        elif status == "unsupported":
            unsupported += 1
        else:
            uncertain += 1
    return supported, unsupported, uncertain, invalid


def build_hybrid_judge_input(
    claim: str,
    evidence: list[dict[str, Any]],
    source_type: str,
    citation_context: str,
) -> dict[str, Any]:
    """Construct the exact JSON object required by the hybrid judge prompt."""
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError("claim must be a non-empty verbatim manuscript sentence.")
    if citation_context not in _ALLOWED_CITATION_CONTEXTS:
        raise ValueError("citation_context must be exactly 'sole' or 'one_of_several'.")
    if source_type not in _ALLOWED_SOURCE_TYPES:
        raise ValueError("source_type must be exactly 'abstract' or 'full_text'.")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("evidence must be a non-empty list of source passages.")

    normalized_evidence = [_coerce_evidence_entry(item) for item in evidence]
    judge_input = {
        "citation_context": citation_context,
        "claim": claim,
        "evidence": normalized_evidence,
        "source_type": source_type,
    }
    # The contract is strict: there must be exactly four top-level fields,
    # each evidence object must have exactly two permitted fields, and no
    # additional provider-specific metadata is allowed in the scientific payload.
    if set(judge_input.keys()) != {"citation_context", "claim", "evidence", "source_type"}:
        raise ValueError("Hybrid judge input must contain exactly four top-level keys: citation_context, claim, evidence, source_type.")
    return judge_input


def _mock_vote(claim: str, evidence: list[dict[str, Any]]) -> str:
    claim_text = (claim or "").lower()
    evidence_text = "\n".join(str(item.get("text", "")) for item in evidence).lower()

    if not claim_text or not evidence_text:
        return "uncertain"

    positive_markers = [
        "reduced", "increased", "improved", "enhanced", "decreased",
        "supports", "supports", "associated with", "led to", "improves"
    ]
    contradiction_markers = [
        "did not", "no effect", "not observed", "no difference", "opposite",
        "instead of", "did not differ", "not significantly", "decreased instead"
    ]

    has_positive = any(marker in claim_text or marker in evidence_text for marker in positive_markers)
    has_contradiction = any(marker in evidence_text for marker in contradiction_markers)
    if has_contradiction and has_positive:
        return "unsupported"
    if has_positive and not has_contradiction:
        return "supported"
    if has_contradiction:
        return "unsupported"
    return "uncertain"


LiveJudgeFatalError = FatalModelCallError


class _OpenAIVoteProvider:
    """One real Chat Completions request per vote, with the settings of MODEL_REQUEST_SETTINGS."""

    _FATAL_STATUS_CODES = {400, 401, 403, 404}

    def __init__(self, client: Any | None = None):
        self._client = client
        self.last_usage: dict[str, int] = {}
        self.last_cached = False

    def _get_client(self) -> Any:
        if self._client is None:
            from src.llm_wrapper import _get_client

            self._client = _get_client()
        return self._client

    def request_vote(self, *, model: str, vote_slot: int, attempt_number: int, judge_input: dict[str, Any], prompt_text: str, request_config: dict[str, Any]) -> Any:
        user_message = serialize_hybrid_judge_input(judge_input)

        def send() -> tuple[str, dict[str, int]]:
            try:
                response = self._get_client().chat.completions.create(
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
                    raise LiveJudgeFatalError(f"OpenAI rejected the request for {model} (HTTP {status}): {exc}") from exc
                raise
            usage = getattr(response, "usage", None)
            return (response.choices[0].message.content or ""), {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            }

        raw, usage, self.last_cached = cached_call(
            model=model, system=prompt_text, user=user_message, slot=f"vote{vote_slot}:attempt{attempt_number}", send=send,
        )
        self.last_usage = usage
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            return json.loads(text)
        except ValueError:
            return text  # validated downstream as an invalid vote, never retried


def _live_hybrid_luna_sol_cascade(
    claim: str,
    evidence: list[dict[str, Any]],
    source_type: str,
    citation_context: str,
    *,
    case_id: str = "PP01-CASE",
) -> dict[str, Any]:
    """Run the real Luna to Sol cascade (6 Luna votes, then 6 Sol votes when Luna does not clear) against the live API."""
    return run_hybrid_luna_sol_cascade(
        claim=claim,
        evidence=evidence,
        source_type=source_type,
        citation_context=citation_context,
        case_id=case_id,
        provider=_OpenAIVoteProvider(),
        live=True,
    )


def run_hybrid_luna_sol_cascade(
    claim: str,
    evidence: list[dict[str, Any]],
    source_type: str,
    citation_context: str,
    *,
    case_id: str = "PP01-CASE",
    provider: Any | None = None,
    live: bool = False,
) -> dict[str, Any]:
    """Run the hybrid judge path.

    live=True means the provider makes real, paid API requests. Without a provider the
    path is a deterministic offline simulation, and it only runs on explicit opt-in.
    """
    if provider is None:
        from src.run_mode import run_mode

        run_mode()  # raises when live and simulation are both enabled
    offline_opt_in = os.getenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "").strip().lower() in {"1", "true", "yes", "on"}
    live_mode = os.getenv("REFCHECK_LIVE_JUDGE", "").strip().lower() in {"1", "true", "yes", "on"}
    missing_key = not OPENAI_API_KEY or OPENAI_API_KEY.strip().lower() in {"offline", "mock", "disabled"}

    if provider is None and offline_opt_in:
        pass
    elif provider is None and not missing_key and live_mode:
        return _live_hybrid_luna_sol_cascade(
            claim=claim,
            evidence=evidence,
            source_type=source_type,
            citation_context=citation_context,
            case_id=case_id,
        )

    if not provider and missing_key:
        if offline_opt_in:
            pass
        elif live_mode:
            raise RuntimeError("Missing OPENAI_API_KEY for production judge execution. Production execution is blocked until credentials are supplied.")
        else:
            raise RuntimeError("OPENAI_API_KEY is not set and REFCHECK_ALLOW_OFFLINE_SIMULATION must be explicitly enabled for offline simulation.")

    if provider is None and not offline_opt_in and not live_mode:
        raise RuntimeError(
            "OPENAI_API_KEY is set but neither REFCHECK_LIVE_JUDGE=1 nor REFCHECK_ALLOW_OFFLINE_SIMULATION=1 is set. "
            "Set REFCHECK_LIVE_JUDGE=1 to run the real judge; simulated verdicts are never produced implicitly."
        )

    resolved_case_id = _resolve_case_id_for_pair(claim, evidence, case_id)
    judge_input = build_hybrid_judge_input(claim, evidence, source_type, citation_context)
    prompt_version = _selected_hybrid_version()
    prompt_text = load_hybrid_prompt(citation_context, version=prompt_version)
    if live and "\n" not in prompt_text:
        raise RuntimeError(f"Judge prompt file not found ({prompt_text}); refusing to send a placeholder to the live model.")
    serialized_input = serialize_hybrid_judge_input(judge_input)
    evidence_blocks = judge_input["evidence"]

    def _record_vote(model: str, vote_slot: int, attempt_number: int, response: Any, *, failure_category: str | None = None, validation_status: str = "valid") -> dict[str, Any]:
        model_name = (model or "").strip().lower()
        request_config = _request_settings_for(model_name)
        call_key = f"{resolved_case_id}::{model_name}::vote::{vote_slot}::{attempt_number}"
        if isinstance(response, dict):
            raw_reply = json.dumps(response, ensure_ascii=False)
        else:
            raw_reply = json.dumps({"error": str(response)}, ensure_ascii=False)
        if failure_category is None:
            derived_status, _, _ = _normalize_vote_status(response, evidence_blocks)
        else:
            derived_status = "failed"
        return {
            "case_id": resolved_case_id,
            "model": model_name,
            "model_identity": model_name,
            "arm": "luna" if model_name == "gpt-5.6-luna" else "sol",
            "vote_slot": int(vote_slot),
            "attempt_number": int(attempt_number),
            "call_key": call_key,
            "validation_status": validation_status,
            "valid": validation_status == "valid",
            "failure_category": failure_category,
            "result": derived_status,
            "verdict": derived_status,
            "raw_reply": raw_reply,
            "request_config": request_config,
            "judge_input": judge_input,
            "user_message": serialized_input,
            "system_message": prompt_text,
            "prompt_path": str(prompt_text),
            "prompt_version": prompt_version,
            "paid": live and not getattr(provider, "last_cached", False),
            "offline_mock": not live,
            "prompt_tokens": int((getattr(provider, "last_usage", None) or {}).get("prompt_tokens", 0)) if live else 0,
            "completion_tokens": int((getattr(provider, "last_usage", None) or {}).get("completion_tokens", 0)) if live else 0,
            "vote_response": response if isinstance(response, dict) else {"error": str(response)},
        }

    luna_votes: list[dict[str, Any]] = []
    luna_statuses: list[str] = []
    call_log: list[dict[str, Any]] = []
    for slot in range(1, 7):
        attempt_number = 1
        try:
            response = _call_provider_vote(provider, model="gpt-5.6-luna", vote_slot=slot, attempt_number=attempt_number, judge_input=judge_input, prompt_text=prompt_text, request_config=_request_settings_for("gpt-5.6-luna"))
        except LiveJudgeFatalError:
            raise
        except Exception as exc:  # pragma: no cover - provider transport errors are recorded here
            call_log.append(_record_vote("gpt-5.6-luna", slot, attempt_number, {"error": str(exc)}, failure_category="transport_failure", validation_status="failed"))
            luna_votes.append({"response": {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [], "explanation": str(exc)}, "status": "failed", "failure_category": "transport_failure"})
            luna_statuses.append("failed")
            continue

        status, failure_category, validation_error = _normalize_vote_status(response, evidence_blocks)
        if failure_category is not None:
            if attempt_number == 1 and _is_non_literal_quote_error(validation_error or ""):
                retry_response = None
                try:
                    retry_response = _call_provider_vote(provider, model="gpt-5.6-luna", vote_slot=slot, attempt_number=2, judge_input=judge_input, prompt_text=prompt_text, request_config=_request_settings_for("gpt-5.6-luna"))
                except LiveJudgeFatalError:
                    raise
                except Exception as exc:
                    retry_response = {"error": str(exc)}
                if isinstance(retry_response, dict):
                    status2, failure2, validation_error2 = _normalize_vote_status(retry_response, evidence_blocks)
                    if failure2 is None:
                        call_log.append(_record_vote("gpt-5.6-luna", slot, 1, response, failure_category="invalid_vote", validation_status="failed"))
                        response = retry_response
                        status = status2
                        failure_category = None
                        validation_error = validation_error2
                        attempt_number = 2
                        call_log.append(_record_vote("gpt-5.6-luna", slot, 2, response, validation_status="valid" if failure_category is None else "failed"))
                        luna_votes.append({"response": response, "status": status, "failure_category": failure_category})
                        luna_statuses.append(status)
                        continue
                call_log.append(_record_vote("gpt-5.6-luna", slot, 1, response, failure_category="invalid_vote", validation_status="failed"))
                call_log.append(_record_vote("gpt-5.6-luna", slot, 2, retry_response if isinstance(retry_response, dict) else {"error": str(retry_response)}, failure_category="invalid_vote", validation_status="failed"))
                luna_votes.append({"response": response, "status": "failed", "failure_category": "invalid_vote"})
                luna_statuses.append("failed")
                continue
            call_log.append(_record_vote("gpt-5.6-luna", slot, attempt_number, response, failure_category="invalid_vote", validation_status="failed"))
            luna_votes.append({"response": response, "status": "failed", "failure_category": "invalid_vote"})
            luna_statuses.append("failed")
            continue

        call_log.append(_record_vote("gpt-5.6-luna", slot, attempt_number, response, validation_status="valid"))
        luna_votes.append({"response": response, "status": status, "failure_category": None})
        luna_statuses.append(status)

    luna_valid = [status for status in luna_statuses if status in {"supported", "unsupported", "uncertain"}]
    if len(luna_valid) == 6 and all(status == "supported" for status in luna_valid):
        final_verdict = "supported"
        flagged = False
        escalated = False
        sol_votes: list[dict[str, Any]] = []
        sol_statuses: list[str] = []
        explanation = _pick_explanation(luna_votes, statuses={"supported"}) or _user_visible_default_explanation()
    else:
        sol_votes = []
        sol_statuses = []
        for slot in range(1, 7):
            attempt_number = 1
            try:
                response = _call_provider_vote(provider, model="gpt-5.6-sol", vote_slot=slot, attempt_number=attempt_number, judge_input=judge_input, prompt_text=prompt_text, request_config=_request_settings_for("gpt-5.6-sol"))
            except LiveJudgeFatalError:
                raise
            except Exception as exc:  # pragma: no cover - provider transport errors are recorded here
                call_log.append(_record_vote("gpt-5.6-sol", slot, attempt_number, {"error": str(exc)}, failure_category="transport_failure", validation_status="failed"))
                sol_votes.append({"response": {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [], "explanation": str(exc)}, "status": "failed", "failure_category": "transport_failure"})
                sol_statuses.append("failed")
                continue

            status, failure_category, validation_error = _normalize_vote_status(response, evidence_blocks)
            if failure_category is not None:
                if attempt_number == 1 and _is_non_literal_quote_error(validation_error or ""):
                    retry_response = None
                    try:
                        retry_response = _call_provider_vote(provider, model="gpt-5.6-sol", vote_slot=slot, attempt_number=2, judge_input=judge_input, prompt_text=prompt_text, request_config=_request_settings_for("gpt-5.6-sol"))
                    except LiveJudgeFatalError:
                        raise
                    except Exception as exc:
                        retry_response = {"error": str(exc)}
                    if isinstance(retry_response, dict):
                        status2, failure2, validation_error2 = _normalize_vote_status(retry_response, evidence_blocks)
                        if failure2 is None:
                            call_log.append(_record_vote("gpt-5.6-sol", slot, 1, response, failure_category="invalid_vote", validation_status="failed"))
                            response = retry_response
                            status = status2
                            failure_category = None
                            validation_error = validation_error2
                            attempt_number = 2
                            call_log.append(_record_vote("gpt-5.6-sol", slot, 2, response, validation_status="valid" if failure_category is None else "failed"))
                            sol_votes.append({"response": response, "status": status, "failure_category": failure_category})
                            sol_statuses.append(status)
                            continue
                    call_log.append(_record_vote("gpt-5.6-sol", slot, 1, response, failure_category="invalid_vote", validation_status="failed"))
                    call_log.append(_record_vote("gpt-5.6-sol", slot, 2, retry_response if isinstance(retry_response, dict) else {"error": str(retry_response)}, failure_category="invalid_vote", validation_status="failed"))
                    sol_votes.append({"response": response, "status": "failed", "failure_category": "invalid_vote"})
                    sol_statuses.append("failed")
                    continue
                call_log.append(_record_vote("gpt-5.6-sol", slot, attempt_number, response, failure_category="invalid_vote", validation_status="failed"))
                sol_votes.append({"response": response, "status": "failed", "failure_category": "invalid_vote"})
                sol_statuses.append("failed")
                continue

            call_log.append(_record_vote("gpt-5.6-sol", slot, attempt_number, response, validation_status="valid"))
            sol_votes.append({"response": response, "status": status, "failure_category": None})
            sol_statuses.append(status)

        supported_count = sum(1 for s in sol_statuses if s == "supported")
        unsupported_count = sum(1 for s in sol_statuses if s == "unsupported")
        if supported_count >= 4:
            final_verdict = "supported"
            flagged = False
            escalated = True
            explanation = _pick_explanation(sol_votes, statuses={"supported"}) or _user_visible_default_explanation()
        elif unsupported_count >= 4:
            final_verdict = "unsupported"
            flagged = False
            escalated = True
            explanation = _pick_explanation(sol_votes, statuses={"unsupported"}) or _user_visible_default_explanation()
        else:
            final_verdict = "uncertain"
            flagged = True
            escalated = True
            explanation = _pick_explanation(sol_votes, statuses={"supported", "unsupported", "uncertain"}) or _user_visible_default_explanation()

    return {
        "judge_executed": True,
        "final_verdict": final_verdict,
        "flagged": flagged,
        "escalated": escalated,
        "case_id": resolved_case_id,
        "luna_votes": luna_statuses,
        "sol_votes": sol_statuses,
        "judge_input": judge_input,
        "serialized_input": serialized_input,
        "user_message": serialized_input,
        "system_message": prompt_text,
        "prompt_version": prompt_version,
        "prompt_path": str(prompt_text),
        "call_log": call_log,
        "offline_mock": not live,
        "simulation": not live,
        "explanation": explanation,
        "observation": explanation,
    }


__all__ = [
    "build_hybrid_judge_input",
    "load_hybrid_prompt",
    "run_hybrid_luna_sol_cascade",
    "serialize_hybrid_judge_input",
    "validate_vote_response",
    "derive_vote_status",
    "process_vote_response_with_retry",
]
