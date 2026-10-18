import io
import json

import app as app_module
import pytest

from orchestrator import runner

JOB_ID = "c" * 32


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "JOB_DIR", tmp_path)
    return app_module.app.test_client()


def test_live_upload_on_render_without_a_data_dir_is_refused_before_anything_is_saved(client, monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", "1")
    monkeypatch.delenv("REFCHECK_DATA_DIR", raising=False)
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")

    response = client.post("/api/upload-manuscript", data={"file": (io.BytesIO(b"x"), "m.pdf")}, content_type="multipart/form-data")

    assert response.status_code == 503
    assert "REFCHECK_DATA_DIR" in response.get_json()["error"]
    assert not (tmp_path / "uploads").exists()


def test_storage_check_is_off_outside_render_or_with_a_data_dir(monkeypatch):
    monkeypatch.setenv("REFCHECK_LIVE_JUDGE", "1")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("REFCHECK_DATA_DIR", raising=False)
    assert app_module._storage_blocker() is None

    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("REFCHECK_DATA_DIR", "/var/data/refcheck")
    assert app_module._storage_blocker() is None


def test_render_yaml_mounts_a_disk_and_points_the_data_dir_at_it():
    text = (app_module.ROOT_DIR / "render.yaml").read_text(encoding="utf-8")

    assert "plan: free" not in text
    assert "mountPath: /var/data" in text
    assert "value: /var/data/refcheck" in text


def test_recovered_report_lists_its_downloadable_files(client, tmp_path):
    job = tmp_path / JOB_ID
    job.mkdir()
    (job / "report.json").write_text(json.dumps({"source_file": "m.pdf"}), encoding="utf-8")
    (job / "judge_log.jsonl").write_text("{}\n", encoding="utf-8")
    app_module.UI_JOBS.clear()

    report = client.get("/api/progress/" + JOB_ID).get_json()["report"]

    assert set(report["output_files"]) == {"report.json", "judge_log.jsonl"}


def test_page_links_every_downloadable_file_and_clears_the_report_when_an_analysis_fails():
    html = (app_module.ROOT_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    for name in app_module.JOB_FILES:
        assert f"['{name}'," in html
    assert "/api/download/" in html
    run_failures = html.split("function pollJob", 1)[1].split("function restoreSavedState", 1)[0]
    assert run_failures.count("clearReport();") >= 3
    assert "clearReport();" in html.split("fetch('/api/upload-manuscript'", 1)[1]


def _uncertain_pair(**extra):
    pair = {
        "ref_number": 2, "status": "verdict", "verdict": "uncertain", "show_verification": True,
        "explanation": "The abstract reports a different endpoint.", "evidence_quotes": ["Cortisol did not change."],
        "evidence_blocks": [{"location": "abstract", "text": "Cortisol did not change."}],
        "sentence_text": "Cortisol rose [2].",
    }
    return {**pair, **extra}


def _normalize(pair):
    report = {"sentences": [{"sentence_id": "s1", "sentence_text": "Cortisol rose [2].", "pairs": [pair]}]}
    refs = [{"number": 2, "raw": "Smith, A. (2019). Title. J. 1, 1.", "doi": "10.1/x"}]
    return runner._normalize_library_report(report, refs)


def test_abstract_only_uncertain_pair_becomes_the_neutral_full_text_card():
    normalized = _normalize(_uncertain_pair())

    assert normalized["cases"] == []
    (card,) = normalized["verification_cases"]
    assert card["kind"] == "verification" and card["error_type"] == "full_text_check_recommended"
    assert card["reference_id"] == 2 and card["marker"] == "[2]"
    assert card["observation"] == runner._FULL_TEXT_CHECK_TEXT
    assert card["claim"] == "Cortisol rose [2]."


def _body_pair(not_established, **extra):
    return _uncertain_pair(
        source_type="full_text", sol_not_established_votes=not_established, sol_valid_votes=6,
        evidence_blocks=[{"location": "body", "text": "Cortisol did not change."}], **extra,
    )


def test_body_evidence_with_four_not_established_sol_votes_becomes_the_orange_card():
    normalized = _normalize(_body_pair(4))

    (card,) = normalized["verification_cases"]
    assert card["error_type"] == "potentially_unsupported"
    assert card["observation"] == (
        "The reviewed evidence did not establish support for this claim. Check it carefully against the cited source."
    )


def test_other_uncertain_pairs_stay_in_the_record_without_a_card():
    normalized = _normalize(_body_pair(3))

    assert normalized["verification_cases"] == []
    (record,) = normalized["uncertain_pairs"]
    assert record["shown_in_report"] is False and record["card_type"] is None
    assert record["sol_not_established_votes"] == 3 and record["body_evidence_reviewed"] is True
    assert record["explanation"] == "The abstract reports a different endpoint."


def test_verification_cards_carry_no_explanation_or_evidence():
    for pair in (_uncertain_pair(), _body_pair(5)):
        (card,) = _normalize(pair)["verification_cases"]
        assert "evidence" not in card and "explanation" not in card
        assert "different endpoint" not in json.dumps(card)


def test_hidden_references_get_no_card_but_are_still_recorded():
    normalized = _normalize(_uncertain_pair(show_verification=False))

    assert normalized["verification_cases"] == []
    assert normalized["uncertain_pairs"][0]["shown_in_report"] is False


def test_page_has_two_summary_cards_and_the_two_verification_labels():
    html = (app_module.ROOT_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert html.count('class="stat" data-filter=') == 2
    assert 'data-filter="verification"' not in html
    assert "Full-text check recommended" in html and "Potentially unsupported claim" in html
    body = html.split("function renderCaseBody(c)", 1)[1].split("function renderCaseCard", 1)[0]
    verification_branch = body.split("if (c.kind === 'verification') {", 1)[1].split("\n    }\n", 1)[0]
    assert "c.evidence" not in verification_branch and "c.explanation" not in verification_branch


def test_errors_detected_and_require_verification_are_counted_separately():
    cases = [{"reference_id": 1}, {"reference_id": 1}, {"reference_id": "orphan:9", "count_as_reference": False}]
    verification = [{"reference_id": 2}, {"reference_id": 3}, {"reference_id": 3}]

    assert runner._report_counts(cases, verification) == {"errors_detected": 1, "require_verification": 2}
