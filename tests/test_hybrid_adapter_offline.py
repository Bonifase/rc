import json
from pathlib import Path

import pytest

from src.hybrid_judge_adapter import (build_hybrid_judge_input,
                                      load_hybrid_prompt,
                                      run_hybrid_luna_sol_cascade,
                                      serialize_hybrid_judge_input)
from src.llm_wrapper import get_verdict

ROOT = Path(__file__).resolve().parents[1]


def test_build_hybrid_judge_input_has_expected_fields():
    payload = build_hybrid_judge_input(
        claim="A citation-bearing sentence copied verbatim from the manuscript.",
        evidence=[{"location": "abstract", "text": "This text is the exact source evidence."}],
        source_type="abstract",
        citation_context="sole",
    )

    assert set(payload.keys()) == {"citation_context", "claim", "evidence", "source_type"}
    assert payload["citation_context"] == "sole"
    assert payload["source_type"] == "abstract"
    assert payload["claim"] == "A citation-bearing sentence copied verbatim from the manuscript."
    assert payload["evidence"] == [{"location": "abstract", "text": "This text is the exact source evidence."}]
    assert payload["evidence"][0]["location"] == "abstract"


def test_run_hybrid_luna_sol_cascade_records_offline_calls(monkeypatch):
    monkeypatch.setenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "1")
    result = run_hybrid_luna_sol_cascade(
        claim="The exercise programme reduced inflammatory markers such as CRP and IL-6.",
        evidence=[{"location": "abstract", "text": "After 12 weeks, the exercise programme reduced plasma C-reactive protein (CRP) and interleukin-6 (IL-6)."}],
        source_type="abstract",
        citation_context="sole",
    )

    assert result["judge_executed"] is True
    assert result["final_verdict"] in {"supported", "unsupported", "uncertain"}
    assert result["call_log"]
    assert all(isinstance(call.get("paid"), bool) for call in result["call_log"])
    assert all(bool(call.get("call_key")) for call in result["call_log"])
    assert all(call.get("model") in {"gpt-5.6-luna", "gpt-5.6-sol"} for call in result["call_log"])


def test_get_verdict_uses_offline_hybrid_fallback_when_api_key_missing(monkeypatch):
    monkeypatch.setattr("src.llm_wrapper.OPENAI_API_KEY", "")
    monkeypatch.setattr("src.llm_wrapper.OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "1")

    import src.hybrid_judge_adapter as hybrid_judge_adapter

    def fake_run_hybrid_luna_sol_cascade(*, claim, evidence, source_type, citation_context, **kwargs):
        return {
            "final_verdict": "supported",
            "explanation": "stubbed offline verdict for no-key fallback test",
        }

    monkeypatch.setattr(hybrid_judge_adapter, "run_hybrid_luna_sol_cascade", fake_run_hybrid_luna_sol_cascade)

    result = get_verdict(
        "The study reduced insulin resistance.",
        "After 12 weeks, insulin resistance decreased significantly in the intervention group.",
        "abstract",
        "sole",
    )

    assert result.status == "verdict"
    assert result.verdict == "supported"
    assert result.source_type == "abstract"
    assert result.citation_context == "sole"
    assert result.explanation == "stubbed offline verdict for no-key fallback test"


def test_run_hybrid_luna_sol_cascade_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setattr("src.hybrid_judge_adapter.OPENAI_API_KEY", "")
    monkeypatch.delenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", raising=False)
    monkeypatch.delenv("REFCHECK_LIVE_JUDGE", raising=False)

    with pytest.raises(RuntimeError, match="explicit opt-in|OPENAI_API_KEY"):
        run_hybrid_luna_sol_cascade(
            claim="Exercise reduced CRP and IL-6 after 12 weeks.",
            evidence=[{"location": "abstract", "text": "After 12 weeks, the exercise programme reduced plasma C-reactive protein and interleukin-6."}],
            source_type="abstract",
            citation_context="sole",
        )


def test_requirement2_single_and_multicitation_inputs_are_source_specific():
    sole_input = build_hybrid_judge_input(
        claim="The exercise programme reduced inflammatory markers in the cohort.",
        evidence=[{"location": "abstract", "text": "After 12 weeks, the exercise programme reduced CRP and IL-6."}],
        source_type="abstract",
        citation_context="sole",
    )
    multicita_input = build_hybrid_judge_input(
        claim="The exercise programme reduced inflammatory markers in the cohort.",
        evidence=[{"location": "abstract", "text": "The intervention lowered CRP in the trial."}],
        source_type="abstract",
        citation_context="one_of_several",
    )

    assert sole_input["citation_context"] == "sole"
    assert multicita_input["citation_context"] == "one_of_several"
    assert sole_input["evidence"][0]["text"] != multicita_input["evidence"][0]["text"]
    assert list(sole_input.keys()) == ["citation_context", "claim", "evidence", "source_type"]
    assert list(multicita_input.keys()) == ["citation_context", "claim", "evidence", "source_type"]


def test_requirement2_prompt_routing_and_serialization_are_deterministic():
    prompt_root = ROOT / "prompts"
    sole_prompt = load_hybrid_prompt("sole", prompt_root=prompt_root)
    multicita_prompt = load_hybrid_prompt("one_of_several", prompt_root=prompt_root)

    assert sole_prompt == (prompt_root / "judge_hybrid_sole_v2_2_2.txt").read_text(encoding="utf-8")
    assert multicita_prompt == (prompt_root / "judge_hybrid_multicita_v2_2_2.txt").read_text(encoding="utf-8")

    judge_input = build_hybrid_judge_input(
        claim="Café-based intervention improved survival in the cohort.",
        evidence=[{"location": "body", "text": "The cohort showed improved survival after the intervention."}],
        source_type="full_text",
        citation_context="sole",
    )
    serialized = serialize_hybrid_judge_input(judge_input)
    assert serialized == json.dumps(judge_input, indent=2, ensure_ascii=False)
    assert "Café" in serialized
    assert "\\u00e9" not in serialized


def test_nonliteral_quote_is_rejected_as_invalid_vote():
    from src.hybrid_judge_adapter import validate_vote_response

    evidence = [{"location": "abstract", "text": "After 12 weeks, the exercise programme reduced plasma C-reactiveprotein and interleukin-6."}]
    response = {
        "evidence_sufficient": True,
        "material_error_present": False,
        "evidence_quotes": ["After 12 weeks, the exercise programme reduced plasma C-reactive protein and interleukin-6."],
        "explanation": "This should be rejected because the quote is not literal in the evidence.",
    }

    with pytest.raises(ValueError, match="literal|substring"):
        validate_vote_response(response, evidence)


def test_requirement2_invalid_contracts_are_rejected():
    bad_evidence_cases = [
        [{"location": "abstract", "text": "ok", "extra": True}],
        [{"location": "methods", "text": "not allowed"}],
        [],
        [{"location": "abstract", "text": " "}],
    ]

    for evidence in bad_evidence_cases:
        with pytest.raises(ValueError):
            build_hybrid_judge_input(
                claim="The manuscript claims this effect.",
                evidence=evidence,
                source_type="abstract",
                citation_context="sole",
            )

    with pytest.raises(ValueError):
        build_hybrid_judge_input(
            claim="The manuscript claims this effect.",
            evidence=[{"location": "abstract", "text": "literal text"}],
            source_type="abstract_only",
            citation_context="sole",
        )

    with pytest.raises(ValueError):
        build_hybrid_judge_input(
            claim="The manuscript claims this effect.",
            evidence=[{"location": "abstract", "text": "literal text"}],
            source_type="abstract",
            citation_context="multicita",
        )


def test_requirement2_frozen_input_is_reused_for_every_vote_and_every_call(monkeypatch):
    monkeypatch.setenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "1")
    judge_input = build_hybrid_judge_input(
        claim="The intervention increased survival significantly in the trial.",
        evidence=[{"location": "abstract", "text": "The intervention increased survival significantly in the trial."}],
        source_type="abstract",
        citation_context="sole",
    )
    result = run_hybrid_luna_sol_cascade(
        claim=judge_input["claim"],
        evidence=judge_input["evidence"],
        source_type=judge_input["source_type"],
        citation_context=judge_input["citation_context"],
    )

    assert result["judge_input"] == judge_input
    assert result["user_message"] == json.dumps(judge_input, indent=2, ensure_ascii=False)
    assert result["system_message"] == load_hybrid_prompt("sole", prompt_root=ROOT / "prompts")
    assert result["serialized_input"] == result["user_message"]
    for entry in result["call_log"]:
        assert entry["judge_input"] == judge_input
        assert entry["user_message"] == result["user_message"]
        assert entry["system_message"] == result["system_message"]


def test_requirement2_missing_evidence_and_unicode_are_handled_honestly():
    with pytest.raises(ValueError):
        build_hybrid_judge_input(
            claim="This claim cannot be judged without evidence.",
            evidence=[],
            source_type="abstract",
            citation_context="sole",
        )

    unicode_input = build_hybrid_judge_input(
        claim="Café résumé — naïve and nuanced values were tracked.",
        evidence=[{"location": "body", "text": "Résumé détaillé: café, naïve, and nuanced values were tracked."}],
        source_type="full_text",
        citation_context="one_of_several",
    )
    serialized = serialize_hybrid_judge_input(unicode_input)
    assert "Résumé" in serialized
    assert "naïve" in serialized
    assert "\\u00e9" not in serialized


class _FakeOpenAIClient:
    """Records every request; no network. Replies are popped per model, the last one repeats."""

    def __init__(self, replies_by_model, error=None):
        self.requests = []
        self._replies = replies_by_model
        self._error = error
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        from types import SimpleNamespace

        self.requests.append(kwargs)
        if self._error is not None:
            raise self._error
        replies = self._replies[kwargs["model"]]
        content = replies.pop(0) if len(replies) > 1 else replies[0]
        usage = SimpleNamespace(prompt_tokens=120, completion_tokens=30)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


def _live_env(monkeypatch, client):
    import src.hybrid_judge_adapter as hybrid_judge_adapter

    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", "1")
    monkeypatch.setenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", "0")
    monkeypatch.setattr(hybrid_judge_adapter, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("src.llm_wrapper._get_client", lambda: client)
    return hybrid_judge_adapter


LIVE_EVIDENCE = [{"location": "abstract", "text": "The study reported no reduction in CRP."}]
LIVE_UNSUPPORTED = json.dumps({
    "evidence_sufficient": True, "material_error_present": True,
    "evidence_quotes": ["no reduction in CRP"], "explanation": "The source reports no reduction.",
})
LIVE_SUPPORTED = json.dumps({
    "evidence_sufficient": True, "material_error_present": False,
    "evidence_quotes": ["no reduction in CRP"], "explanation": "The source matches.",
})


def test_live_cascade_sends_real_requests_with_the_frozen_settings_and_escalates(monkeypatch):
    client = _FakeOpenAIClient({"gpt-5.6-luna": [LIVE_UNSUPPORTED], "gpt-5.6-sol": [LIVE_UNSUPPORTED]})
    adapter = _live_env(monkeypatch, client)

    result = adapter._live_hybrid_luna_sol_cascade(
        claim="The study found a reduction in CRP.", evidence=LIVE_EVIDENCE,
        source_type="abstract", citation_context="sole", case_id="CASE-LIVE",
    )

    models = [request["model"] for request in client.requests]
    assert models == ["gpt-5.6-luna"] * 6 + ["gpt-5.6-sol"] * 6
    assert result["final_verdict"] == "unsupported"
    assert result["offline_mock"] is False and result["simulation"] is False
    assert all(entry["paid"] is True and entry["offline_mock"] is False for entry in result["call_log"])
    assert all(entry["prompt_tokens"] == 120 and entry["completion_tokens"] == 30 for entry in result["call_log"])

    luna, sol = client.requests[0], client.requests[6]
    for request in (luna, sol):
        assert request["max_completion_tokens"] == 8000
        assert not {"max_tokens", "temperature", "seed", "response_format", "reasoning_effort"} & set(request)
        assert request["messages"][0]["content"] == load_hybrid_prompt("sole")
        assert request["messages"][1]["content"] == json.dumps(result["judge_input"], indent=2, ensure_ascii=False)
    assert luna["timeout"] == 60 and sol["timeout"] == 300


def test_live_cascade_stops_after_six_clean_luna_votes(monkeypatch):
    client = _FakeOpenAIClient({"gpt-5.6-luna": [LIVE_SUPPORTED]})
    adapter = _live_env(monkeypatch, client)

    result = adapter._live_hybrid_luna_sol_cascade(
        claim="The study found no change in CRP.", evidence=LIVE_EVIDENCE,
        source_type="abstract", citation_context="sole",
    )

    assert len(client.requests) == 6
    assert result["final_verdict"] == "supported"
    assert result["escalated"] is False


def test_live_cascade_aborts_immediately_when_the_provider_rejects_the_request(monkeypatch):
    class Rejected(Exception):
        status_code = 401

    client = _FakeOpenAIClient({}, error=Rejected("Incorrect API key provided"))
    adapter = _live_env(monkeypatch, client)

    with pytest.raises(adapter.LiveJudgeFatalError, match="HTTP 401"):
        adapter._live_hybrid_luna_sol_cascade(
            claim="The study found a reduction in CRP.", evidence=LIVE_EVIDENCE,
            source_type="abstract", citation_context="sole",
        )
    assert len(client.requests) == 1


def test_live_cascade_treats_a_non_json_reply_as_an_invalid_vote_without_retrying(monkeypatch):
    client = _FakeOpenAIClient({"gpt-5.6-luna": ["not json"], "gpt-5.6-sol": ["not json"]})
    adapter = _live_env(monkeypatch, client)

    result = adapter._live_hybrid_luna_sol_cascade(
        claim="The study found a reduction in CRP.", evidence=LIVE_EVIDENCE,
        source_type="abstract", citation_context="sole",
    )

    assert len(client.requests) == 12
    assert result["final_verdict"] == "uncertain"
    assert {entry["failure_category"] for entry in result["call_log"]} == {"invalid_vote"}


def test_a_set_key_never_falls_back_to_a_silent_simulation(monkeypatch):
    import src.hybrid_judge_adapter as hybrid_judge_adapter

    monkeypatch.setattr(hybrid_judge_adapter, "OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("REFCHECK_LIVE_JUDGE", raising=False)
    monkeypatch.delenv("REFCHECK_ALLOW_OFFLINE_SIMULATION", raising=False)

    with pytest.raises(RuntimeError, match="REFCHECK_LIVE_JUDGE=1"):
        hybrid_judge_adapter.run_hybrid_luna_sol_cascade(
            claim="The study found a reduction in CRP.", evidence=LIVE_EVIDENCE,
            source_type="abstract", citation_context="sole",
        )


def test_run_hybrid_luna_sol_cascade_uses_live_path_when_enabled(monkeypatch):
    import src.hybrid_judge_adapter as hybrid_judge_adapter

    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", "true")
    monkeypatch.setattr(hybrid_judge_adapter, "OPENAI_API_KEY", "sk-test")

    live_result = {
        "judge_executed": True,
        "final_verdict": "supported",
        "explanation": "The source support matches the manuscript claim.",
        "observation": "The source support matches the manuscript claim.",
    }
    monkeypatch.setattr(hybrid_judge_adapter, "_live_hybrid_luna_sol_cascade", lambda **kwargs: live_result)

    result = hybrid_judge_adapter.run_hybrid_luna_sol_cascade(
        claim="The source support matches the manuscript claim.",
        evidence=[{"location": "abstract", "text": "The intervention improved outcomes."}],
        source_type="abstract",
        citation_context="sole",
    )

    assert result["explanation"] == live_result["explanation"]
    assert result["observation"] == live_result["observation"]


class FakeProvider:
    def __init__(self, responses_by_model):
        self.responses_by_model = {model: list(votes) for model, votes in responses_by_model.items()}
        self.calls = []

    def request_vote(self, *, model, vote_slot, attempt_number, judge_input, prompt_text, request_config):
        response = self.responses_by_model.get(model, []).pop(0) if self.responses_by_model.get(model, []) else {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [], "explanation": "fallback invalid vote"}
        call_key = f"{model}::vote::{vote_slot}::{attempt_number}"
        self.calls.append({
            "model": model,
            "vote_slot": vote_slot,
            "attempt_number": attempt_number,
            "call_key": call_key,
            "request_config": request_config,
            "response": response,
        })
        return response


EVIDENCE_TEXT = "The intervention improved outcomes by 20%."


def _supported_vote(text=EVIDENCE_TEXT):
    return {
        "evidence_sufficient": True,
        "material_error_present": False,
        "evidence_quotes": [text],
        "explanation": "The source supports the claim.",
    }


def _unsupported_vote(text=EVIDENCE_TEXT):
    return {
        "evidence_sufficient": True,
        "material_error_present": True,
        "evidence_quotes": [text],
        "explanation": "The source contradicts the claim.",
    }


def _uncertain_vote():
    return {
        "evidence_sufficient": False,
        "material_error_present": False,
        "evidence_quotes": [],
        "explanation": "The evidence is insufficient or mixed.",
    }


def test_requirement4_luna_6_supported_short_circuits_and_sol_is_not_called():
    provider = FakeProvider({
        "gpt-5.6-luna": [_supported_vote() for _ in range(6)],
        "gpt-5.6-sol": [],
    })

    result = run_hybrid_luna_sol_cascade(
        claim="The intervention improved outcomes.",
        evidence=[{"location": "abstract", "text": EVIDENCE_TEXT}],
        source_type="abstract",
        citation_context="sole",
        provider=provider,
    )

    assert result["final_verdict"] == "supported"
    assert len(result["call_log"]) == 6
    assert all(entry["model"] == "gpt-5.6-luna" for entry in result["call_log"])
    assert provider.calls and all(call["model"] == "gpt-5.6-luna" for call in provider.calls)
    assert result["explanation"] == "The source supports the claim."


def test_requirement2_exact_vote_contract_is_valid_and_missing_or_extra_keys_are_invalid():
    valid = _supported_vote()
    assert set(valid.keys()) == {"evidence_sufficient", "material_error_present", "evidence_quotes", "explanation"}

    missing = {"evidence_sufficient": True, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT]}
    extra = {**valid, "offline_mock": True}

    valid_entry = json.loads(json.dumps(valid))
    invalid_missing_entry = json.loads(json.dumps(missing))
    invalid_extra_entry = json.loads(json.dumps(extra))

    assert run_hybrid_luna_sol_cascade(
        claim="The intervention improved outcomes.",
        evidence=[{"location": "abstract", "text": EVIDENCE_TEXT}],
        source_type="abstract",
        citation_context="sole",
        provider=FakeProvider({"gpt-5.6-luna": [valid_entry], "gpt-5.6-sol": []}),
    )["call_log"][0]["valid"] is True

    invalid_result = run_hybrid_luna_sol_cascade(
        claim="The intervention improved outcomes.",
        evidence=[{"location": "abstract", "text": EVIDENCE_TEXT}],
        source_type="abstract",
        citation_context="sole",
        provider=FakeProvider({"gpt-5.6-luna": [invalid_missing_entry], "gpt-5.6-sol": []}),
    )
    assert invalid_result["call_log"][0]["valid"] is False
    assert invalid_result["call_log"][0]["failure_category"] == "invalid_vote"

    invalid_extra_result = run_hybrid_luna_sol_cascade(
        claim="The intervention improved outcomes.",
        evidence=[{"location": "abstract", "text": EVIDENCE_TEXT}],
        source_type="abstract",
        citation_context="sole",
        provider=FakeProvider({"gpt-5.6-luna": [invalid_extra_entry], "gpt-5.6-sol": []}),
    )
    assert invalid_extra_result["call_log"][0]["valid"] is False
    assert invalid_extra_result["call_log"][0]["failure_category"] == "invalid_vote"


def test_requirement4_sol_escalates_when_luna_does_not_reach_six_supported_votes():
    provider = FakeProvider({
        "gpt-5.6-luna": [
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _uncertain_vote(),
        ],
        "gpt-5.6-sol": [
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _unsupported_vote(),
            _unsupported_vote(),
        ],
    })

    result = run_hybrid_luna_sol_cascade(
        claim="The intervention improved outcomes.",
        evidence=[{"location": "abstract", "text": EVIDENCE_TEXT}],
        source_type="abstract",
        citation_context="sole",
        provider=provider,
    )

    assert result["escalated"] is True
    assert result["final_verdict"] == "supported"
    assert sum(1 for entry in result["call_log"] if entry["model"] == "gpt-5.6-sol") == 6


def test_requirement4_explanation_preserves_the_judge_observation_not_the_cascade_summary():
    provider = FakeProvider({
        "gpt-5.6-luna": [
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _unsupported_vote(),
        ],
        "gpt-5.6-sol": [
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
        ],
    })

    result = run_hybrid_luna_sol_cascade(
        claim="The intervention improved outcomes.",
        evidence=[{"location": "abstract", "text": EVIDENCE_TEXT}],
        source_type="abstract",
        citation_context="sole",
        provider=provider,
    )

    assert result["final_verdict"] == "supported"
    assert result["explanation"] == "The source supports the claim."
    assert "Luna cleared all six vote slots" not in result["explanation"]
    assert "Sol did not reach a four-vote majority" not in result["explanation"]


def test_requirement4_no_cascade_summary_when_vote_response_has_no_scientific_explanation():
    provider = FakeProvider({
        "gpt-5.6-luna": [
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
        ],
        "gpt-5.6-sol": [
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [EVIDENCE_TEXT], "explanation": ""},
        ],
    })

    result = run_hybrid_luna_sol_cascade(
        claim="The intervention improved outcomes.",
        evidence=[{"location": "abstract", "text": EVIDENCE_TEXT}],
        source_type="abstract",
        citation_context="sole",
        provider=provider,
    )

    assert result["final_verdict"] == "uncertain"
    assert result["explanation"] == "The judge did not return a scientific explanation for this case."
    assert "Luna cleared all six vote slots" not in result["explanation"]
    assert "Sol did not reach a four-vote majority" not in result["explanation"]


def test_requirement4_quote_retry_stays_on_same_vote_slot():
    provider = FakeProvider({
        "gpt-5.6-luna": [
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            {
                "evidence_sufficient": True,
                "material_error_present": False,
                "evidence_quotes": ["not a literal substring of evidence"],
                "explanation": "bad quote",
            },
            {
                "evidence_sufficient": True,
                "material_error_present": False,
                "evidence_quotes": [EVIDENCE_TEXT],
                "explanation": "retry repaired the literal quote.",
            },
        ],
    })

    result = run_hybrid_luna_sol_cascade(
        claim="The intervention improved outcomes.",
        evidence=[{"location": "abstract", "text": EVIDENCE_TEXT}],
        source_type="abstract",
        citation_context="sole",
        provider=provider,
    )

    assert result["final_verdict"] == "supported"
    vote_slots = sorted({call["vote_slot"] for call in provider.calls})
    assert vote_slots == [1, 2, 3, 4, 5, 6]
    assert len([call for call in provider.calls if call["vote_slot"] == 6]) == 2
    assert len({call["call_key"] for call in provider.calls}) == len(provider.calls)


def test_requirement4_sol_thresholds_and_invalid_votes_do_not_count_as_unsupported():
    provider = FakeProvider({
        "gpt-5.6-luna": [
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _uncertain_vote(),
        ],
        "gpt-5.6-sol": [
            _supported_vote(),
            _supported_vote(),
            _supported_vote(),
            _unsupported_vote(),
            {"evidence_sufficient": False, "material_error_present": False, "evidence_quotes": [], "explanation": "invalid"},
            _supported_vote(),
        ],
    })

    result = run_hybrid_luna_sol_cascade(
        claim="The intervention improved outcomes.",
        evidence=[{"location": "abstract", "text": EVIDENCE_TEXT}],
        source_type="abstract",
        citation_context="sole",
        provider=provider,
    )

    assert result["final_verdict"] == "supported"
    assert sum(1 for entry in result["call_log"] if entry["model"] == "gpt-5.6-sol") == 6
    assert any(entry["failure_category"] == "invalid_vote" for entry in result["call_log"])


def test_requirement4_request_settings_and_disabled_provider_access_are_safe():
    from src.hybrid_judge_adapter import MODEL_REQUEST_SETTINGS

    for model, settings in MODEL_REQUEST_SETTINGS.items():
        assert model in {"gpt-5.6-luna", "gpt-5.6-sol"}
        assert settings["timeout_seconds"] in {60, 300}
        assert settings["max_completion_tokens"] == 8000
        assert settings["sdk_max_retries"] == 0
        assert settings["temperature"] is None
        assert settings["seed"] is None
        assert settings["reasoning"] is None
        assert settings["response_format"] is None

    provider = FakeProvider({"gpt-5.6-luna": [_supported_vote() for _ in range(6)]})
    result = run_hybrid_luna_sol_cascade(
        claim="The intervention improved outcomes.",
        evidence=[{"location": "abstract", "text": EVIDENCE_TEXT}],
        source_type="abstract",
        citation_context="sole",
        provider=provider,
    )

    assert result["final_verdict"] == "supported"
    assert result["offline_mock"] is True
    assert all("response_format" not in entry["request_config"] for entry in result["call_log"])
    assert all("temperature" not in entry["request_config"] for entry in result["call_log"])
