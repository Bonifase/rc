import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from openai import RateLimitError

from src.llm_wrapper import get_verdict, REQUIRED_KEYS, _quotes_are_verbatim

EXCERPT = "Cortisol administration markedly increased ubiquitin-proteasome transcripts and total proteolytic activity in white muscle at both 24 and 72 h."


def _valid_response(**overrides):
    base = {
        "source_type": "full_text",
        "citation_context": "sole",
        "assertions_checked": "the whole claim",
        "verdict": "supported",
        "primary_family": None,
        "secondary_family": "none",
        "evidence_quote": "Cortisol administration markedly increased ubiquitin-proteasome transcripts",
        "omitted_contradicting_evidence": "",
        "source_attribution": "own_finding",
        "explanation": "The excerpt directly supports the claim.",
    }
    base.update(overrides)
    return base


def _mock_response(content: str, prompt_tokens: int = 0, completion_tokens: int = 0, cached_tokens: int = 0):
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
    )
    return mock_response


def _mock_rate_limit_error():
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    return RateLimitError(message="rate limited", response=mock_response, body={"error": {"message": "x"}})


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_get_verdict_success_with_valid_quote(mock_openai_cls):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(json.dumps(_valid_response()))
    mock_openai_cls.return_value = mock_client

    result = get_verdict("Cortisol impairs muscle growth by activating proteolysis.", EXCERPT, "full_text", "sole")

    assert result.status == "verdict"
    assert result.verdict == "supported"
    assert result.quote_guard_triggered is False
    assert mock_client.chat.completions.create.call_count == 1

    # Confirm the payload sent matches the required 4-field shape.
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    user_message = json.loads(call_kwargs["messages"][1]["content"])
    assert set(user_message.keys()) == {"claim", "excerpt", "source_type", "citation_context"}
    assert user_message["source_type"] == "full_text"
    assert user_message["citation_context"] == "sole"


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_get_verdict_sends_real_system_prompt(mock_openai_cls):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(json.dumps(_valid_response()))
    mock_openai_cls.return_value = mock_client

    get_verdict("claim", EXCERPT, "full_text", "sole")

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    system_message = call_kwargs["messages"][0]
    assert system_message["role"] == "system"
    # Confirm it's the real prompt, not a placeholder -- checks for a
    # distinctive phrase from the actual file.
    assert "scientific fact-checking assistant" in system_message["content"]


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_missing_required_key_is_system_error(mock_openai_cls):
    bad_response = _valid_response()
    del bad_response["explanation"]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(json.dumps(bad_response))
    mock_openai_cls.return_value = mock_client

    result = get_verdict("claim", EXCERPT, "full_text", "sole")

    assert result.status == "system_error"
    assert "missing required keys" in result.error_message


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_invalid_verdict_value_is_system_error(mock_openai_cls):
    bad_response = _valid_response(verdict="maybe")
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(json.dumps(bad_response))
    mock_openai_cls.return_value = mock_client

    result = get_verdict("claim", EXCERPT, "full_text", "sole")

    assert result.status == "system_error"
    assert "unrecognized verdict" in result.error_message


# ---------------------------------------------------------------------------
# Guard 1: evidence_quote / omitted_contradicting_evidence must be verbatim
# substrings of the excerpt actually sent.
# ---------------------------------------------------------------------------

@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_fabricated_quote_retried_then_succeeds_on_second_call(mock_openai_cls):
    bad = _valid_response(evidence_quote="complete gibberish that matches nothing in the source")
    good = _valid_response()  # valid quote
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        _mock_response(json.dumps(bad)),
        _mock_response(json.dumps(good)),
    ]
    mock_openai_cls.return_value = mock_client

    result = get_verdict("claim", EXCERPT, "full_text", "sole")

    assert result.status == "verdict"
    assert result.quote_guard_triggered is False
    assert result.evidence_quote == good["evidence_quote"]
    assert mock_client.chat.completions.create.call_count == 2

    # The retry must be a blind re-roll of the identical prompt: a corrective
    # message made the live model double down on the same malformed quote.
    retry_message = mock_client.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"]
    first_message = mock_client.chat.completions.create.call_args_list[0].kwargs["messages"][1]["content"]
    assert retry_message == first_message


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_multi_span_quote_with_elision_is_accepted(mock_openai_cls):
    # The model sometimes joins two separated passages with "[...]". Every
    # part is a verbatim substring; the whole string is not. The guard must
    # accept it -- nothing fabricated is shown -- and not fail the pair.
    response = _valid_response(evidence_quote=(
        "Cortisol administration markedly increased ubiquitin-proteasome transcripts "
        "[...] total proteolytic activity in white muscle at both 24 and 72 h."
    ))
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(json.dumps(response))
    mock_openai_cls.return_value = mock_client

    result = get_verdict("claim", EXCERPT, "full_text", "sole")

    assert result.status == "verdict"
    assert result.quote_guard_triggered is False
    assert mock_client.chat.completions.create.call_count == 1


def test_multi_span_quote_with_fabricated_part_still_fails():
    quote = (
        "Cortisol administration markedly increased ubiquitin-proteasome transcripts "
        "[...] hepatic gluconeogenesis doubled in white muscle"
    )
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), EXCERPT) is False


def test_multi_span_quote_with_dot_ellipsis_also_accepted():
    quote = (
        "Cortisol administration markedly increased ubiquitin-proteasome transcripts"
        "... total proteolytic activity in white muscle at both 24 and 72 h."
    )
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), EXCERPT) is True


def test_quote_joining_parts_across_source_ellipsis_is_accepted():
    # The retrieval layer splits some abstracts into parts with a "[...]"
    # line. The model may quote across such a boundary and silently drop the
    # marker, joining two verbatim passages into one continuous span. Every
    # word is still verbatim, so the guard must accept it -- the live C3/C4
    # clean controls (Faught & Vijayan 2021/2020) failed here and surfaced
    # false System errors on correct "supported" verdicts.
    excerpt = (
        "An acute stress resulted in the hyperactivity of the larvae in light at "
        "15, 60, and 240 min post-stress, and this was absent in CRHR1-KO larvae.\n"
        "[...]\n"
        "The stress-induced hyperactivity was absent in the MRKO fish, while GRKO "
        "mutants showed transient effects."
    )
    quote = (
        "An acute stress resulted in the hyperactivity of the larvae in light at "
        "15, 60, and 240 min post-stress, and this was absent in CRHR1-KO larvae. "
        "The stress-induced hyperactivity was absent in the MRKO fish, while GRKO "
        "mutants showed transient effects."
    )
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), excerpt) is True


def test_quote_across_source_ellipsis_with_fabricated_word_still_fails():
    excerpt = (
        "Part one of the passage is here.\n"
        "[...]\n"
        "Part two of the passage is here."
    )
    quote = (
        "Part one of the passage is here. "
        "Part two of the passage is here but with fabricated words."
    )
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), excerpt) is False


def test_quote_compressing_sentences_across_a_dropped_middle_is_accepted():
    # Dense single-paragraph abstracts: the model quotes several verbatim
    # source sentences and silently drops an intervening sentence, with no
    # "[...]" marker (the Faught & Vijayan 2021 clean control C3). Every
    # quoted sentence is real source text in order, so the guard must accept
    # the faithful compression instead of surfacing a false System error.
    excerpt = (
        "An acute stress resulted in the hyperactivity of the larvae in light at 15, "
        "60, and 240 min post-stress, and this was absent in CRHR1-KO larvae. "
        "Addition of human CRH peptide caused hyperactivity at 15 min, and this "
        "response was also abolished in the CRHR1-KO mutants. The stress-induced "
        "hyperactivity was absent in the MRKO fish, while GRKO mutants showed "
        "transient effects."
    )
    quote = (
        "An acute stress resulted in the hyperactivity of the larvae in light at 15, "
        "60, and 240 min post-stress, and this was absent in CRHR1-KO larvae. "
        "The stress-induced hyperactivity was absent in the MRKO fish, while GRKO "
        "mutants showed transient effects."
    )
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), excerpt) is True


def test_quote_dropping_negator_word_inside_one_sentence_still_fails():
    # Sentence-level elision must not let a single-source-sentence quote skip
    # a word to flip its meaning: "had no effect" quoted as "had effect" is
    # one non-contiguous piece, not a whole-sentence drop, so it stays invalid.
    excerpt = (
        "The GR-null larvae showed no stress response, while the MR-null larvae "
        "retained a delayed but sustained response."
    )
    quote = (
        "The GR-null larvae showed stress response, while the MR-null larvae "
        "retained a delayed but sustained response."
    )
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), excerpt) is False


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_paraphrased_omitted_evidence_is_dropped_not_fatal(mock_openai_cls):
    # The omitted_contradicting_evidence field is model commentary that is
    # never shown to the user. A paraphrase there (a rephrasing of real source
    # content) must be dropped rather than sink the whole pair -- the live C2
    # clean control (Sturm 2004) surfaced a false System error here.
    response = _valid_response(
        omitted_contradicting_evidence="receptor transcripts were ubiquitous across the tissues examined"
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(json.dumps(response))
    mock_openai_cls.return_value = mock_client

    result = get_verdict("claim", EXCERPT, "full_text", "sole")

    assert result.status == "verdict"
    assert result.quote_guard_triggered is False
    assert result.omitted_contradicting_evidence == ""
    assert mock_client.chat.completions.create.call_count == 1


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_fabricated_quote_persists_after_retry_is_system_error_not_uncertain(mock_openai_cls):
    bad = _valid_response(
        evidence_quote="fabricated wording that is not present in the source",
        verdict="supported",
        primary_family=None,
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(json.dumps(bad))
    mock_openai_cls.return_value = mock_client

    result = get_verdict("claim", EXCERPT, "full_text", "sole")

    assert result.status == "system_error"
    assert result.quote_guard_triggered is True
    assert mock_client.chat.completions.create.call_count == 2  # original + one re-roll


def test_fabricated_omitted_evidence_fails_strict_verbatim_check():
    # The strict guard still fails a fabricated omitted field (get_verdict is
    # lenient about it -- it is dropped -- but the strict checker must never
    # silently accept fabricated text).
    bad = _valid_response(omitted_contradicting_evidence="text that is not in the excerpt")
    assert _quotes_are_verbatim(bad, EXCERPT) is False


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_empty_quote_with_verdict_other_than_unsupported_is_system_error(mock_openai_cls):
    response = _valid_response(evidence_quote="", omitted_contradicting_evidence="")
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(json.dumps(response))
    mock_openai_cls.return_value = mock_client

    result = get_verdict("claim", EXCERPT, "full_text", "sole")

    assert result.status == "system_error"
    assert result.quote_guard_triggered is True


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_empty_quote_is_valid_only_for_unsupported_off_target_families(mock_openai_cls):
    response = _valid_response(
        evidence_quote="",
        omitted_contradicting_evidence="",
        verdict="unsupported",
        primary_family="off_target_relevance",
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(json.dumps(response))
    mock_openai_cls.return_value = mock_client

    result = get_verdict("claim", EXCERPT, "full_text", "sole")

    assert result.status == "verdict"
    assert result.verdict == "unsupported"
    assert result.quote_guard_triggered is False
    assert mock_client.chat.completions.create.call_count == 1


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_empty_quote_invalid_for_unsupported_without_off_target_family(mock_openai_cls):
    response = _valid_response(
        evidence_quote="",
        omitted_contradicting_evidence="",
        verdict="unsupported",
        primary_family="sentence_supports_claim",
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(json.dumps(response))
    mock_openai_cls.return_value = mock_client

    result = get_verdict("claim", EXCERPT, "full_text", "sole")

    assert result.status == "system_error"
    assert result.quote_guard_triggered is True


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_short_quote_shorter_than_10_chars_is_system_error(mock_openai_cls):
    response = _valid_response(evidence_quote="Gibberish")
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(json.dumps(response))
    mock_openai_cls.return_value = mock_client

    result = get_verdict("claim", EXCERPT, "full_text", "sole")

    assert result.status == "system_error"
    assert result.quote_guard_triggered is True


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_retry_call_itself_fails_is_still_system_error(mock_openai_cls):
    bad = _valid_response(evidence_quote="complete nonsense that matches nothing")
    mock_client = MagicMock()
    # original call (1) + retry's 3 tenacity attempts (3) = 4 create() calls.
    mock_client.chat.completions.create.side_effect = [
        _mock_response(json.dumps(bad)),
        _mock_rate_limit_error(),
        _mock_rate_limit_error(),
        _mock_rate_limit_error(),
    ]
    mock_openai_cls.return_value = mock_client

    result = get_verdict("claim", EXCERPT, "full_text", "sole")

    assert result.status == "system_error"
    assert result.quote_guard_triggered is True


@patch("src.llm_wrapper.OPENAI_API_KEY", "")
def test_raises_clear_error_when_no_api_key_configured():
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_verdict("claim", EXCERPT, "full_text", "sole")


def test_required_keys_match_prompt_output_schema():
    # Sanity check that the schema we validate against matches what the
    # prompt file actually promises to return.
    assert REQUIRED_KEYS == {
        "source_type", "citation_context", "assertions_checked", "verdict",
        "primary_family", "secondary_family", "evidence_quote",
        "omitted_contradicting_evidence", "source_attribution", "explanation",
    }


# ---------------------------------------------------------------------------
# Guard 1 normalization is notation-insensitive: the model often re-renders
# the source's scientific notation (x -> ×, +/- -> ±, 10(-10) -> superscripts,
# hyphens -> dashes, straight -> curly quotes). A faithful quote must still
# pass; only quotes that change the actual words may fail.
# ---------------------------------------------------------------------------

SCI_EXCERPT = (
    "The mineralocorticoids 11-deoxycorticosterone and aldosterone were more potent "
    "enhancers of rtMRa transcriptional activity (EC50 = 1.6 +/- 0.5 x 10(-10) and "
    "1.1 +/- 0.4 x 10(-10) M, respectively) than the glucocorticoids cortisol and "
    "11-deoxycortisol (EC50 = 1.1 +/- 0.3 x 10(-9) and 3.7 +/- 1.9 x 10(-9) M, "
    "respectively)"
)


def test_quote_with_rendered_unicode_notation_matches_excerpt():
    # Source spells it "1.6 +/- 0.5 x 10(-10)"; the model renders it with
    # ×, ± and superscript ⁻¹⁰. Same words, different notation -- must pass.
    quote = (
        "The mineralocorticoids 11-deoxycorticosterone and aldosterone were more potent "
        "enhancers of rtMRa transcriptional activity (EC50 = 1.6 \u00b1 0.5 \u00d7 "
        "10\u207b\u00b9\u2070 and 1.1 \u00b1 0.4 \u00d7 10\u207b\u00b9\u2070 M, respectively)"
    )
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), SCI_EXCERPT) is True


def test_quote_with_dash_and_quote_variants_matches_excerpt():
    # en-dash instead of hyphen, curly quotes instead of straight, NBSP
    # instead of space -- all formatting noise.
    quote = (
        "Cortisol administration markedly increased ubiquitin\u2013proteasome transcripts "
        "and total proteolytic activity in white\u00a0muscle at both 24 and 72\u00a0h."
    )
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), EXCERPT) is True


def test_quote_with_micro_symbol_variant_matches_excerpt():
    # "µm" (µ = U+00B5) must match "um".
    excerpt = "Myofibre density fell by 37% in fish exposed to 10 um of the analogue for 16 weeks of treatment."
    quote = "Myofibre density fell by 37% in fish exposed to 10 \u00b5m of the analogue for 16 weeks of treatment."
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), excerpt) is True


def test_quote_with_altered_word_still_fails_after_normalization():
    # Normalization must not be so loose that changed words slip through.
    quote = (
        "Cortisol administration markedly increased ubiquitin-proteasome transcripts "
        "and total proteolytic activity in white muscular"
    )
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), EXCERPT) is False


def test_fabricated_quote_still_fails_after_normalization():
    quote = "Cortisol administration markedly increased hepatic gluconeogenesis in white muscle"
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), EXCERPT) is False


def test_reordered_quote_still_fails_after_normalization():
    # Same words, different order: not verbatim, must not pass.
    quote = (
        "total proteolytic activity and ubiquitin-proteasome transcripts increased "
        "markedly with cortisol administration in white muscle"
    )
    assert _quotes_are_verbatim(_valid_response(evidence_quote=quote), EXCERPT) is False


# ---------------------------------------------------------------------------
# Structured Outputs -- the schema sent to OpenAI must exactly mirror the
# prompt's declared Output block, and must actually be sent on every call.
# ---------------------------------------------------------------------------

def test_response_schema_matches_required_keys_exactly():
    from src.llm_wrapper import _RESPONSE_SCHEMA
    schema = _RESPONSE_SCHEMA["json_schema"]["schema"]
    assert set(schema["properties"].keys()) == REQUIRED_KEYS
    assert set(schema["required"]) == REQUIRED_KEYS
    assert schema["additionalProperties"] is False


def test_response_schema_is_valid_json_schema():
    import jsonschema
    from src.llm_wrapper import _RESPONSE_SCHEMA
    jsonschema.Draft7Validator.check_schema(_RESPONSE_SCHEMA["json_schema"]["schema"])


def test_get_verdict_sends_response_format_on_every_call():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(_valid_response())
    mock_client.chat.completions.create.return_value = mock_response

    with patch("src.llm_wrapper._get_client", return_value=mock_client):
        get_verdict("A claim.", EXCERPT, "full_text", "sole")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["strict"] is True


def test_client_disables_sdk_internal_retries_to_avoid_double_backoff():
    """
    The SDK's own default retry (max_retries=2) would stack on top of our
    tenacity wrapper around _call_and_parse, which already retries the same
    error types (RateLimitError, APIConnectionError, APITimeoutError). We
    rely on our own retry/backoff exclusively, so the client must be
    constructed with max_retries=0.
    """
    from src.llm_wrapper import _get_client
    with patch("src.llm_wrapper.OPENAI_API_KEY", "sk-test-key"):
        client = _get_client()
    assert client.max_retries == 0


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_get_verdict_captures_usage_tokens(mock_openai_cls):
    """Token counters must flow from the API's usage field into the result,
    so runs can price themselves (incl. the cached prompt prefix)."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(
        json.dumps(_valid_response()), prompt_tokens=15200, completion_tokens=120, cached_tokens=14541
    )
    mock_openai_cls.return_value = mock_client

    result = get_verdict("Cortisol impairs muscle growth by activating proteolysis.", EXCERPT, "full_text", "sole")

    assert result.status == "verdict"
    assert result.prompt_tokens == 15200
    assert result.completion_tokens == 120
    assert result.cached_tokens == 14541


@patch("src.llm_wrapper.OPENAI_API_KEY", "test-key")
@patch("src.llm_wrapper.OpenAI")
def test_guard_reroll_merges_usage_of_both_calls(mock_openai_cls):
    """When the quote guard fires and the re-roll succeeds, the result's
    usage must sum both billed calls (original + re-roll)."""
    mock_client = MagicMock()
    fabricated = _valid_response(
        evidence_quote="This whole sentence is invented text that never appears in the excerpt anywhere."
    )
    mock_client.chat.completions.create.side_effect = [
        _mock_response(json.dumps(fabricated), prompt_tokens=15000, completion_tokens=100, cached_tokens=14541),
        _mock_response(json.dumps(_valid_response()), prompt_tokens=15000, completion_tokens=90, cached_tokens=14541),
    ]
    mock_openai_cls.return_value = mock_client

    result = get_verdict("Cortisol impairs muscle growth by activating proteolysis.", EXCERPT, "full_text", "sole")

    assert result.status == "verdict"
    assert result.quote_guard_triggered is False  # guard fired, re-roll rescued
    assert mock_client.chat.completions.create.call_count == 2
    assert result.prompt_tokens == 30000
    assert result.completion_tokens == 190
    assert result.cached_tokens == 29082
