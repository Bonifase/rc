"""
Step 4 (v2): Send a (claim, excerpt) pair to the LLM per the new comparison
prompt, get back a structured verdict.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import re

from openai import OpenAI, APIError, APIConnectionError, RateLimitError, APITimeoutError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    LLM_MAX_RETRIES,
    LLM_GUARD_REROLL_TEMPERATURE,
    LLM_SEED,
)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "comparison_system_prompt.txt"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

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


def _get_client() -> OpenAI:
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
def _call_and_parse(client: OpenAI, user_message: str, temperature: float = 0, seed: Optional[int] = None) -> dict:
    kwargs = {}
    if seed is not None:
        kwargs["seed"] = seed
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
            response_format=_RESPONSE_SCHEMA,
            **kwargs,
        )
    except APIError as exc:
        raise LlmSystemError(f"OpenAI API error: {exc}") from exc

    raw_text = response.choices[0].message.content or ""
    parsed = _parse_response_json(raw_text)
    parsed["_raw_response"] = raw_text
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
    )


def get_verdict(claim: str, excerpt: str, source_type: str, citation_context: str) -> VerdictResult:
    client = _get_client()
    user_message = _build_user_message(claim, excerpt, source_type, citation_context)

    try:
        data = _call_and_parse(client, user_message, seed=LLM_SEED)
    except (LlmSystemError, RateLimitError, APIConnectionError, APITimeoutError) as exc:
        return VerdictResult(status="system_error", error_message=str(exc))

    data = _drop_paraphrased_omitted_evidence(data, excerpt)
    if _quotes_are_verbatim(data, excerpt):
        return _result_from_data(data)

    try:
        retry_data = _call_and_parse(client, user_message, temperature=LLM_GUARD_REROLL_TEMPERATURE)
    except (LlmSystemError, RateLimitError, APIConnectionError, APITimeoutError):
        retry_data = None

    if retry_data is not None:
        retry_data = _drop_paraphrased_omitted_evidence(retry_data, excerpt)
        if _quotes_are_verbatim(retry_data, excerpt):
            return _result_from_data(retry_data)

    def _snippet(value: Optional[str]) -> str:
        if not value:
            return "<empty>"
        return value[:120].replace("\n", " ") + ("..." if len(value) > 120 else "")

    return VerdictResult(
        status="system_error",
        error_message=(
            "LLM returned an evidence quote that is empty, too short, or "
            "not a verbatim substring of the excerpt, even after retry. "
            f"Quote attempt 1: {_snippet(data.get('evidence_quote'))}; "
            f"attempt 2: {_snippet((retry_data or {}).get('evidence_quote'))}."
        ),
        quote_guard_triggered=True,
    )