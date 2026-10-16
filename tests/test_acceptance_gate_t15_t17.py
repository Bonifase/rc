from src.acceptance_harness import orchestrator_accept, run_verify_bundled
from src.adapter_registry import apply_prompt_swap, get_adapter_config
from src.claims_v36 import claims_v36
from src.derive_pair_verdict_v36 import derive_pair_verdict_v36
from src.judge_v30 import judge_v30


def test_prompt_versions_cover_v2_1_and_v2_2_2():
    config = get_adapter_config()
    names = {item["name"] for item in config["judge_prompt_versions"]}
    assert "judge_v2.1" in names
    assert "judge_v2.2.2" in names


def test_claims_v36_01b_decomposes_supporting_claims():
    claim_set = claims_v36("Exercise reduced CRP and IL-6 after 12 weeks.", decomposition="01b")
    assert claim_set
    assert all("exercise" in item["text"].lower() or "crp" in item["text"].lower() or "il-6" in item["text"].lower() for item in claim_set)


def test_judge_v30_and_pair_verdict_are_offline_safe():
    verdict = derive_pair_verdict_v36(
        "Exercise reduced CRP and IL-6 after 12 weeks.",
        "After 12 weeks, the exercise programme reduced plasma C-reactive protein and interleukin-6.",
        source_type="abstract",
        citation_context="sole",
    )
    assert verdict["verdict"] in {"supported", "unsupported", "uncertain"}

    result = judge_v30(
        "Exercise reduced CRP and IL-6 after 12 weeks.",
        [
            {"location": "abstract", "text": "After 12 weeks, the exercise programme reduced plasma C-reactive protein and interleukin-6."}
        ],
        source_type="abstract",
        citation_context="sole",
    )
    assert result["final_verdict"] in {"supported", "unsupported", "uncertain"}
    assert result["offline_mock"] is True


def test_orchestrator_accept_accepts_offline_replay_and_live_modes():
    replay = orchestrator_accept(mode="replay")
    live = orchestrator_accept(mode="live")

    assert replay["status"] == "accepted"
    assert live["status"] == "accepted"
    assert replay["mode"] == "replay"
    assert live["mode"] == "live"
    assert replay["prompt_versions_used"]
    assert live["prompt_versions_used"]


def test_bundled_verify_script_runs_offline():
    payload = run_verify_bundled()
    assert payload["status"] == "passed"
    assert payload["mode"] == "offline"


def test_prompt_swap_records_selected_version_and_hash():
    payload = apply_prompt_swap("judge", "v2.2.2")
    assert payload["selected_version"] == "judge_v2.2.2"
    assert payload["prompt_hash"]
    assert payload["mode"] == "offline"
