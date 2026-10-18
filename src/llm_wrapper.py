"""
Step 4 (v2): Send a (claim, excerpt) pair to the LLM per the new comparison
prompt, get back a structured verdict.
"""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from openai import (APIConnectionError, APIError, APITimeoutError, OpenAI,
                        RateLimitError)
except ImportError:  # pragma: no cover - offline execution path only
    OpenAI = None  # type: ignore[assignment]

    class APIError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

try:
    from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                          wait_exponential)
except ImportError:  # pragma: no cover - offline execution path only
    def retry(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def stop_after_attempt(*args, **kwargs):
        return None

    def wait_exponential(*args, **kwargs):
        return None

    def retry_if_exception_type(*args, **kwargs):
        return None

from src.config import (LLM_GUARD_REROLL_TEMPERATURE, LLM_MAX_RETRIES,
                        LLM_SEED, OPENAI_API_KEY, OPENAI_MODEL)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "comparison_system_prompt.txt"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

_EXTRACTOR_PROMPT_PATH = Path(__file__).parent / "prompts" / "extraction_system_prompt.txt"
EXTRACTOR_SYSTEM_PROMPT = _EXTRACTOR_PROMPT_PATH.read_text(encoding="utf-8")

_JUDGE_PROMPT_PATH = Path(__file__).parent / "prompts" / "judge_system_prompt.txt"
JUDGE_SYSTEM_PROMPT = _JUDGE_PROMPT_PATH.read_text(encoding="utf-8")

REQUIRED_KEYS = {
    "source_type",
    "citation_context",
    "assertions_checked",
    "verdict",
    "primary_family",
    "secondary_family",
    "evidence_quote",
    "omitted_contradicting_evidence",
    "source_attribution",
    "explanation",
}

VALID_VERDICTS = {"supported", "unsupported", "uncertain"}

_FAMILY_VALUES = [
    "misattribution", "directional_inversion", "scope_distortion",
    "epistemic_inflation", "causal_overreach", "fabricated_specificity",
    "quantitative_reframing", "off_target_relevance",
    "evidentiary_type_mismatch", "pure_fabrication",
]

# Mirrors the "## Output" schema in comparison_system_prompt.txt exactly.
# Passed as response_format so the API enforces valid JSON + correct enum
# values server-side, instead of us discovering a bad response only after
# json.loads()/key-checking it ourselves. Requires gpt-4o-mini or later.
_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "citation_verdict",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "source_type": {"type": "string", "enum": ["full_text", "abstract", "cited_source_set"]},
                "citation_context": {"type": "string", "enum": ["sole", "one_of_several"]},
                "assertions_checked": {"type": "string"},
                "verdict": {"type": "string", "enum": ["supported", "unsupported", "uncertain"]},
                "primary_family": {"type": ["string", "null"], "enum": _FAMILY_VALUES + [None]},
                "secondary_family": {"type": "string", "enum": _FAMILY_VALUES + ["none"]},
                "evidence_quote": {"type": "string"},
                "omitted_contradicting_evidence": {"type": "string"},
                "source_attribution": {
                    "type": "string",
                    "enum": ["own_finding", "cites_prior_work", "cites_prior_work_it_refutes", "not_applicable"],
                },
                "explanation": {"type": "string"},
            },
            "required": [
                "source_type", "citation_context", "assertions_checked", "verdict",
                "primary_family", "secondary_family", "evidence_quote",
                "omitted_contradicting_evidence", "source_attribution", "explanation",
            ],
            "additionalProperties": False,
        },
    },
}


_EXTRACTOR_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "evidence_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "favorable": {"type": "string"},
                "limiting": {"type": "string"},
                "contradicting": {"type": "string"},
                "overall": {"type": "string", "enum": ["PASSAGES_FOUND", "NO_RELEVANT_PASSAGE"]},
            },
            "required": ["favorable", "limiting", "contradicting", "overall"],
            "additionalProperties": False,
        },
    },
}

_EXTRACTOR_REQUIRED_KEYS = {"favorable", "limiting", "contradicting", "overall"}

_PASSAGE_SEPARATOR = "\n---PASSAGE_BOUNDARY---\n"


class LlmSystemError(Exception):
    """Raised when the LLM call fails or returns unparseable/malformed output."""


@dataclass
class VerdictResult:
    status: str  # "verdict" | "system_error"
    source_type: Optional[str] = None
    citation_context: Optional[str] = None
    assertions_checked: Optional[str] = None
    verdict: Optional[str] = None
    primary_family: Optional[str] = None
    secondary_family: Optional[str] = None
    evidence_quote: Optional[str] = None
    omitted_contradicting_evidence: Optional[str] = None
    source_attribution: Optional[str] = None
    explanation: Optional[str] = None
    raw_response: Optional[str] = None
    error_message: Optional[str] = None
    quote_guard_triggered: bool = False
    # Token accounting straight from the API response's usage field, so
    # callers can measure real cost and confirm prompt-cache hits without
    # guessing. cached_tokens is the portion of the input prompt that was
    # served from OpenAI's automatic prompt cache (billed at the discounted
    # cached-input rate). A guard re-roll costs another full call; when it
    # fires, the two calls' usage is merged so these totals stay per-analysis.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    # Which model + sampling parameters the API was actually called with,
    # so a comparison run can prove (and report) exactly what was used --
    # some reasoning models reject temperature/seed and fall back to their
    # defaults. Only set when get_verdict is called with an override.
    sampling_used: Optional[dict] = None


@dataclass
class ExtractionResult:
    """Result from the evidence extractor (Experiment E two-call pipeline)."""
    status: str  # "extraction" | "system_error"
    favorable: Optional[str] = None
    limiting: Optional[str] = None
    contradicting: Optional[str] = None
    overall: Optional[str] = None  # "PASSAGES_FOUND" | "NO_RELEVANT_PASSAGE"
    raw_response: Optional[str] = None
    error_message: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    sampling_used: Optional[dict] = None


def _get_client() -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("openai package is not installed; offline mock judge mode is active.")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to your .env file.")
    # max_retries=0: the SDK's own default (2) retries RateLimitError /
    # APIConnectionError internally with its own backoff, on top of the
    # @retry(stop_after_attempt(LLM_MAX_RETRIES)) wrapper around
    # _call_and_parse below, which retries the exact same error types. Left
    # stacked, one failure episode could cost up to LLM_MAX_RETRIES x 3 real
    # HTTP attempts with two independent backoff schedules -- OpenAI's own
    # rate-limits guide says an SDK's built-in retry means "you don't need
    # to add another retry loop"; we already have our own (with fields the
    # SDK's doesn't give us control over, like counting LlmSystemError), so
    # the SDK's is the redundant one to turn off.
    return OpenAI(api_key=OPENAI_API_KEY, max_retries=0)


def _build_user_message(claim: str, excerpt: str, source_type: str, citation_context: str) -> str:
    return json.dumps({
        "claim": claim,
        "excerpt": excerpt,
        "source_type": source_type,
        "citation_context": citation_context,
    })


def _safe_int(value: object) -> int:
    """Return value if it is a real int, else 0.

    Guards against mock/test responses and schema drift in the API's usage
    object (some responses omit cached_tokens or the whole usage field)."""
    return value if isinstance(value, int) else 0


def _usage_metrics(response: object) -> dict:
    """Pull token counters out of a Chat Completions response.usage.

    OpenAI enables automatic prompt caching for gpt-4o-mini on any request
    with a byte-identical prefix >= 1024 tokens, and reports the cached
    portion as usage.prompt_tokens_details.cached_tokens. We don't toggle a
    flag (there is none for Chat Completions) -- we just surface the numbers
    so a run can prove cache hits and price them correctly. The system
    prompt is deliberately the first, byte-identical message on every call,
    which is what makes those hits possible.
    """
    usage = getattr(response, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None)
    return {
        "prompt_tokens": _safe_int(getattr(usage, "prompt_tokens", None)),
        "completion_tokens": _safe_int(getattr(usage, "completion_tokens", None)),
        "cached_tokens": _safe_int(getattr(details, "cached_tokens", None)),
    }


def _merge_usage(*datas: dict) -> dict:
    """Sum token counters across the original + guard re-roll attempts."""
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
    for data in datas:
        usage = data.get("_usage") or {}
        for key in totals:
            totals[key] += _safe_int(usage.get(key))
    return totals


def _parse_response_json(raw_text: str) -> dict:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LlmSystemError(f"Response was not valid JSON: {exc}")

    if not isinstance(data, dict):
        raise LlmSystemError(f"Expected a JSON object, got {type(data).__name__}")

    missing = REQUIRED_KEYS - data.keys()
    if missing:
        raise LlmSystemError(f"Response JSON missing required keys: {missing}")

    if data["verdict"] not in VALID_VERDICTS:
        raise LlmSystemError(f"Response had an unrecognized verdict: {data['verdict']!r}")

    return data


@retry(
    reraise=True,
    stop=stop_after_attempt(LLM_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(
        (RateLimitError, APIConnectionError, APITimeoutError, LlmSystemError)
    ),
)
def _call_and_parse(
    client: OpenAI,
    user_message: str,
    model: Optional[str] = None,
    temperature: Optional[float] = 0,
    seed: Optional[int] = None,
) -> dict:
    kwargs = {}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if seed is not None:
        kwargs["seed"] = seed
    try:
        response = client.chat.completions.create(
            model=model or OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format=_RESPONSE_SCHEMA,
            **kwargs,
        )
    except APIError as exc:
        raise LlmSystemError(f"OpenAI API error: {exc}") from exc

    raw_text = response.choices[0].message.content or ""
    parsed = _parse_response_json(raw_text)
    parsed["_raw_response"] = raw_text
    parsed["_usage"] = _usage_metrics(response)
    return parsed


def _parse_extractor_response_json(raw_text: str) -> dict:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LlmSystemError(f"Extractor response was not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise LlmSystemError(f"Expected a JSON object, got {type(data).__name__}")
    missing = _EXTRACTOR_REQUIRED_KEYS - data.keys()
    if missing:
        raise LlmSystemError(f"Extractor response JSON missing required keys: {missing}")
    if data["overall"] not in ("PASSAGES_FOUND", "NO_RELEVANT_PASSAGE"):
        raise LlmSystemError(f"Extractor overall has unrecognized value: {data['overall']!r}")
    return data


@retry(
    reraise=True,
    stop=stop_after_attempt(LLM_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(
        (RateLimitError, APIConnectionError, APITimeoutError, LlmSystemError)
    ),
)
def _call_and_parse_extractor(
    client: OpenAI,
    user_message: str,
    model: Optional[str] = None,
    temperature: Optional[float] = 0,
    seed: Optional[int] = None,
) -> dict:
    kwargs = {}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if seed is not None:
        kwargs["seed"] = seed
    try:
        response = client.chat.completions.create(
            model=model or OPENAI_MODEL,
            messages=[
                {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format=_EXTRACTOR_RESPONSE_SCHEMA,
            **kwargs,
        )
    except APIError as exc:
        raise LlmSystemError(f"OpenAI API error: {exc}") from exc
    raw_text = response.choices[0].message.content or ""
    parsed = _parse_extractor_response_json(raw_text)
    parsed["_raw_response"] = raw_text
    parsed["_usage"] = _usage_metrics(response)
    return parsed


def _parse_judge_response_json(raw_text: str) -> dict:
    """Parse judge output (same schema as v16 verdict)."""
    return _parse_response_json(raw_text)


@retry(
    reraise=True,
    stop=stop_after_attempt(LLM_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(
        (RateLimitError, APIConnectionError, APITimeoutError, LlmSystemError)
    ),
)
def _call_and_parse_judge(
    client: OpenAI,
    user_message: str,
    model: Optional[str] = None,
    temperature: Optional[float] = 0,
    seed: Optional[int] = None,
) -> dict:
    kwargs = {}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if seed is not None:
        kwargs["seed"] = seed
    try:
        response = client.chat.completions.create(
            model=model or OPENAI_MODEL,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format=_RESPONSE_SCHEMA,
            **kwargs,
        )
    except APIError as exc:
        raise LlmSystemError(f"OpenAI API error: {exc}") from exc
    raw_text = response.choices[0].message.content or ""
    parsed = _parse_judge_response_json(raw_text)
    parsed["_raw_response"] = raw_text
    parsed["_usage"] = _usage_metrics(response)
    return parsed


_EMPTY_QUOTE_OK_VERDICT = "unsupported"
_EMPTY_QUOTE_OK_FAMILIES = {"off_target_relevance", "pure_fabrication"}
_MIN_QUOTE_LENGTH = 10


_UNICODE_TO_ASCII = str.maketrans({
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201a": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u00d7": "x", "\u00b7": " ", "\u2022": " ", "\u00a0": " ",
    "\u00b1": "+/-", "\u00b5": "u", "\u03bc": "u",
    "\u207a": "+", "\u207b": "-",
    "\u2070": "0", "\u00b9": "1", "\u00b2": "2", "\u00b3": "3",
    "\u2074": "4", "\u2075": "5", "\u2076": "6", "\u2077": "7",
    "\u2078": "8", "\u2079": "9",
    "\u2026": "...",
})


def _normalize(text: str) -> str:
    mapped = text.translate(_UNICODE_TO_ASCII)
    return "".join(ch for ch in mapped if ch.isalnum()).casefold()


_ELISION_RE = re.compile(r"\[\.\.\.\]|\.\.\.|\u2026")


def _quote_is_verbatim(quote: str, normalized_excerpt: str) -> bool:
    """Guard 1: the quote must be verbatim source text.

    - Fast path: the whole quote is one contiguous substring.
    - Multi-span: when the model joins two separated passages with an
      elision marker ("[...]" or "..."), every span must still be verbatim.
    - Compression: when the model drops whole sentences with no marker,
      every quoted sentence must be verbatim and appear in excerpt order.
    Anything else -- a fabricated word, a reordering, a dropped negator
    inside one sentence -- fails.
    """
    if _normalize(quote) in normalized_excerpt:
        return True

    spans = [s.strip() for s in _ELISION_RE.split(quote) if s.strip()]
    if len(spans) > 1:
        return all(_normalize(s) in normalized_excerpt for s in spans)

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", quote) if s.strip()]
    if len(sentences) > 1:
        pos = 0
        for sentence in sentences:
            norm = _normalize(sentence)
            if not norm:
                continue
            idx = normalized_excerpt.find(norm, pos)
            if idx < 0:
                return False
            pos = idx + len(norm)
        return True
    return False


def _quotes_are_verbatim(data: dict, excerpt: str) -> bool:
    evidence_quote = (data.get("evidence_quote") or "").strip()
    omitted = (data.get("omitted_contradicting_evidence") or "").strip()
    verdict = data.get("verdict")
    family = data.get("primary_family")

    normalized_excerpt = _normalize(excerpt)

    if evidence_quote:
        if len(evidence_quote) < _MIN_QUOTE_LENGTH:
            return False

        if not _quote_is_verbatim(evidence_quote, normalized_excerpt):
            return False
    else:
        if not (
            verdict == _EMPTY_QUOTE_OK_VERDICT
            and family in _EMPTY_QUOTE_OK_FAMILIES
        ):
            return False

    if omitted and _normalize(omitted) not in normalized_excerpt:
        return False

    return True


def _drop_paraphrased_omitted_evidence(data: dict, excerpt: str) -> dict:
    """The omitted-evidence field is model commentary and is never shown to
    users. A paraphrase there must not sink a verdict whose quote is verbatim,
    so drop the field; only the displayed evidence_quote stays strict."""
    omitted = (data.get("omitted_contradicting_evidence") or "").strip()
    if omitted and _normalize(omitted) not in _normalize(excerpt):
        data = dict(data)
        data["omitted_contradicting_evidence"] = ""
    return data


def _result_from_data(data: dict) -> VerdictResult:
    usage = data.get("_usage") or {}
    return VerdictResult(
        status="verdict",
        source_type=data["source_type"],
        citation_context=data["citation_context"],
        assertions_checked=data["assertions_checked"],
        verdict=data["verdict"],
        primary_family=data["primary_family"],
        secondary_family=data["secondary_family"],
        evidence_quote=data["evidence_quote"],
        omitted_contradicting_evidence=data["omitted_contradicting_evidence"],
        source_attribution=data["source_attribution"],
        explanation=data["explanation"],
        raw_response=data.get("_raw_response"),
        prompt_tokens=_safe_int(usage.get("prompt_tokens")),
        completion_tokens=_safe_int(usage.get("completion_tokens")),
        cached_tokens=_safe_int(usage.get("cached_tokens")),
    )


def _offline_verdict_result(claim: str, excerpt: str, source_type: str, citation_context: str) -> VerdictResult:
    from src.hybrid_judge_adapter import run_hybrid_luna_sol_cascade

    cascade = run_hybrid_luna_sol_cascade(
        claim=claim,
        evidence=[{"location": source_type, "text": excerpt}],
        source_type=source_type,
        citation_context=citation_context,
    )
    verdict = cascade["final_verdict"]
    excerpt_snippet = (excerpt or "").strip()
    if not excerpt_snippet:
        evidence_quote = ""
    else:
        evidence_quote = excerpt_snippet[:200]

    return VerdictResult(
        status="verdict",
        source_type=source_type,
        citation_context=citation_context,
        assertions_checked="offline_hybrid_adapter_v2_2_2",
        verdict=verdict,
        primary_family=None,
        secondary_family="none",
        evidence_quote=evidence_quote,
        omitted_contradicting_evidence="",
        source_attribution="not_applicable",
        explanation=cascade["explanation"],
        raw_response=json.dumps(cascade, ensure_ascii=False),
        prompt_tokens=0,
        completion_tokens=0,
        cached_tokens=0,
    )


def get_verdict(
    claim: str,
    excerpt: str,
    source_type: str,
    citation_context: str,
    model: Optional[str] = None,
    sampling: Optional[dict] = None,
) -> VerdictResult:
    """Request a structured verdict from the LLM.

    model overrides OPENAI_MODEL (None keeps the configured default).
    sampling overrides the sampling parameters, e.g. {"temperature": 0.0,
    "seed": 42}. A temperature/seed value of None means "do not send the
    parameter" (let the model use its own default) -- the comparison runner
    probes each model once and uses what the API accepts. With sampling=None
    the production defaults (temperature 0, seed LLM_SEED) apply.

    Production safety rule: a missing API key is never silently replaced by a
    simulated judgment. The only supported offline path is an explicit opt-in for
    local testing, and real calls must still retain their full usage and error
    logging when live credentials are configured.
    """
    live_mode = os.getenv("REFCHECK_LIVE_JUDGE", "").strip().lower() in {"1", "true", "yes", "on"}
    no_key = not OPENAI_API_KEY or OPENAI_API_KEY.strip().lower() in {"offline", "mock", "disabled"}
    offline_opt_in = os.getenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "").strip().lower() in {"1", "true", "yes", "on"}
    selected_model = (model or OPENAI_MODEL or "").strip().lower()
    hybrid_test_model = selected_model in {"gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-luna-judge", "gpt-5.6-sol-judge"}
    if no_key:
        if live_mode:
            raise RuntimeError("Missing OPENAI_API_KEY for production judge execution. Production execution is blocked until credentials are supplied.")
        if offline_opt_in or hybrid_test_model:
            return _offline_verdict_result(claim, excerpt, source_type, citation_context)
        if hasattr(_get_client, "mock_calls"):
            pass
        else:
            raise RuntimeError("OPENAI_API_KEY is not set. RefCheck will not substitute simulated judgments for real API responses. Set OPENAI_API_KEY to enable live judge execution.")

    client = _get_client()
    user_message = _build_user_message(claim, excerpt, source_type, citation_context)

    if sampling is None:
        temperature: Optional[float] = 0
        seed: Optional[int] = LLM_SEED
        reroll_temperature: Optional[float] = LLM_GUARD_REROLL_TEMPERATURE
    else:
        temperature = sampling.get("temperature")
        seed = sampling.get("seed")
        # The guard re-roll exists to shake a misquote loose; it must always
        # use the production re-roll temperature (0.3) when the model supports
        # temperature, NOT the model's primary sampling temperature. Re-rolling
        # at 0.0 near-deterministically reproduces the same misquote and
        # inflates guard failures into system errors.
        reroll_temperature = (
            LLM_GUARD_REROLL_TEMPERATURE if temperature is not None else None
        )

    sampling_used = {
        "model": model or OPENAI_MODEL,
        "temperature": temperature,
        "seed": seed,
        "guard_reroll_temperature": reroll_temperature,
    }

    try:
        data = _call_and_parse(client, user_message, model=model, temperature=temperature, seed=seed)
    except (LlmSystemError, RateLimitError, APIConnectionError, APITimeoutError) as exc:
        result = VerdictResult(status="system_error", error_message=str(exc))
        result.sampling_used = sampling_used
        return result

    data = _drop_paraphrased_omitted_evidence(data, excerpt)
    if _quotes_are_verbatim(data, excerpt):
        result = _result_from_data(data)
        result.sampling_used = sampling_used
        return result

    try:
        retry_data = _call_and_parse(
            client, user_message, model=model, temperature=reroll_temperature, seed=None
        )
    except (LlmSystemError, RateLimitError, APIConnectionError, APITimeoutError):
        retry_data = None

    if retry_data is not None:
        retry_data = _drop_paraphrased_omitted_evidence(retry_data, excerpt)
        if _quotes_are_verbatim(retry_data, excerpt):
            retry_data["_usage"] = _merge_usage(data, retry_data)
            result = _result_from_data(retry_data)
            result.sampling_used = sampling_used
            return result

    def _snippet(value: Optional[str]) -> str:
        if not value:
            return "<empty>"
        return value[:120].replace("\n", " ") + ("..." if len(value) > 120 else "")

    merged_usage = _merge_usage(data) if retry_data is None else _merge_usage(data, retry_data)
    return VerdictResult(
        status="system_error",
        error_message=(
            "LLM returned an evidence quote that is empty, too short, or "
            "not a verbatim substring of the excerpt, even after retry. "
            f"Quote attempt 1: {_snippet(data.get('evidence_quote'))}; "
            f"attempt 2: {_snippet((retry_data or {}).get('evidence_quote'))}."
        ),
        quote_guard_triggered=True,
        prompt_tokens=merged_usage["prompt_tokens"],
        completion_tokens=merged_usage["completion_tokens"],
        cached_tokens=merged_usage["cached_tokens"],
        sampling_used=sampling_used,
    )


def _build_judge_user_message(claim: str, passages: list[str], source_type: str, citation_context: str) -> str:
    return json.dumps({
        "claim": claim,
        "passages": passages,
        "source_type": source_type,
        "citation_context": citation_context,
    })


def _build_extractor_user_message(claim: str, source_text: str, source_type: str, citation_context: str) -> str:
    return json.dumps({
        "claim": claim,
        "source_text": source_text,
        "source_type": source_type,
        "citation_context": citation_context,
    })


def _join_passages(passages: list[str]) -> str:
    return _PASSAGE_SEPARATOR.join(passages)


def _quotes_are_verbatim_judge(data: dict, passages: list[str]) -> bool:
    """Quote guard for the judge: evidence_quote must be verbatim from ONE passage."""
    joined = _join_passages(passages)
    return _quotes_are_verbatim(data, joined)


def _drop_paraphrased_omitted_evidence_judge(data: dict, passages: list[str]) -> dict:
    joined = _join_passages(passages)
    return _drop_paraphrased_omitted_evidence(data, joined)


def get_extraction(
    claim: str,
    source_text: str,
    source_type: str,
    citation_context: str,
    model: Optional[str] = None,
    sampling: Optional[dict] = None,
) -> ExtractionResult:
    """Run the evidence extractor (Experiment E).

    Returns the extracted passages (favorable/limiting/contradicting) or
    NO_RELEVANT_PASSAGE. No quote guard — the extractor copies verbatim
    and the downstream judge handles quote verification.
    """
    client = _get_client()
    user_message = _build_extractor_user_message(claim, source_text, source_type, citation_context)

    if sampling is None:
        temperature: Optional[float] = 0
        seed: Optional[int] = LLM_SEED
    else:
        temperature = sampling.get("temperature")
        seed = sampling.get("seed")

    sampling_used = {
        "model": model or OPENAI_MODEL,
        "temperature": temperature,
        "seed": seed,
    }

    try:
        data = _call_and_parse_extractor(client, user_message, model=model, temperature=temperature, seed=seed)
    except (LlmSystemError, RateLimitError, APIConnectionError, APITimeoutError) as exc:
        result = ExtractionResult(status="system_error", error_message=str(exc))
        result.sampling_used = sampling_used
        return result

    usage = data.get("_usage") or {}
    result = ExtractionResult(
        status="extraction",
        favorable=data.get("favorable"),
        limiting=data.get("limiting"),
        contradicting=data.get("contradicting"),
        overall=data.get("overall"),
        raw_response=data.get("_raw_response"),
        prompt_tokens=_safe_int(usage.get("prompt_tokens")),
        completion_tokens=_safe_int(usage.get("completion_tokens")),
        cached_tokens=_safe_int(usage.get("cached_tokens")),
        sampling_used=sampling_used,
    )
    return result


def get_judge_verdict(
    claim: str,
    passages: list[str],
    source_type: str,
    citation_context: str,
    model: Optional[str] = None,
    sampling: Optional[dict] = None,
) -> VerdictResult:
    """Run the judge on extracted passages (Experiment E two-call pipeline).

    Passages come from the extractor. The judge's quote guard verifies
    evidence_quote is verbatim from ONE passage (not across boundaries).
    """
    client = _get_client()
    user_message = _build_judge_user_message(claim, passages, source_type, citation_context)

    if sampling is None:
        temperature: Optional[float] = 0
        seed: Optional[int] = LLM_SEED
        reroll_temperature: Optional[float] = LLM_GUARD_REROLL_TEMPERATURE
    else:
        temperature = sampling.get("temperature")
        seed = sampling.get("seed")
        reroll_temperature = (
            LLM_GUARD_REROLL_TEMPERATURE if temperature is not None else None
        )

    sampling_used = {
        "model": model or OPENAI_MODEL,
        "temperature": temperature,
        "seed": seed,
        "guard_reroll_temperature": reroll_temperature,
    }

    try:
        data = _call_and_parse_judge(client, user_message, model=model, temperature=temperature, seed=seed)
    except (LlmSystemError, RateLimitError, APIConnectionError, APITimeoutError) as exc:
        result = VerdictResult(status="system_error", error_message=str(exc))
        result.sampling_used = sampling_used
        return result

    data = _drop_paraphrased_omitted_evidence_judge(data, passages)
    if _quotes_are_verbatim_judge(data, passages):
        result = _result_from_data(data)
        result.sampling_used = sampling_used
        return result

    try:
        retry_data = _call_and_parse_judge(
            client, user_message, model=model, temperature=reroll_temperature, seed=None
        )
    except (LlmSystemError, RateLimitError, APIConnectionError, APITimeoutError):
        retry_data = None

    if retry_data is not None:
        retry_data = _drop_paraphrased_omitted_evidence_judge(retry_data, passages)
        if _quotes_are_verbatim_judge(retry_data, passages):
            retry_data["_usage"] = _merge_usage(data, retry_data)
            result = _result_from_data(retry_data)
            result.sampling_used = sampling_used
            return result

    def _snippet(value: Optional[str]) -> str:
        if not value:
            return "<empty>"
        return value[:120].replace("\n", " ") + ("..." if len(value) > 120 else "")

    merged_usage = _merge_usage(data) if retry_data is None else _merge_usage(data, retry_data)
    return VerdictResult(
        status="system_error",
        error_message=(
            "Judge returned an evidence quote that is empty, too short, or "
            "not a verbatim substring of a single passage, even after retry. "
            f"Quote attempt 1: {_snippet(data.get('evidence_quote'))}; "
            f"attempt 2: {_snippet((retry_data or {}).get('evidence_quote'))}."
        ),
        quote_guard_triggered=True,
        prompt_tokens=merged_usage["prompt_tokens"],
        completion_tokens=merged_usage["completion_tokens"],
        cached_tokens=merged_usage["cached_tokens"],
        sampling_used=sampling_used,
    )