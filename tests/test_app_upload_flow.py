import io
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import app


def _make_docx_bytes(body_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>',
        )
        zf.writestr("docProps/core.xml", "")
        zf.writestr("docProps/app.xml", "")
        zf.writestr(
            "word/document.xml",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{body_text}</w:t></w:r></w:p>
  </w:body>
</w:document>''',
        )
    return buf.getvalue()


def _make_pdf_bytes(body_text: str) -> bytes:
    text = body_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("latin-1"))
    return bytes(pdf)


@patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False)
def test_upload_manuscript_starts_job_and_returns_report():
    client = app.test_client()
    docx_bytes = _make_docx_bytes(
        "This claim is supported by prior work [1].\n\nReferences\n[1] Smith A. Example Paper. 2020. https://doi.org/10.1000/example"
    )

    response = client.post(
        "/api/upload-manuscript",
        data={"file": (io.BytesIO(docx_bytes), "sample.docx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert "job_id" in payload
    job_id = payload["job_id"]

    progress = None
    for _ in range(30):
        progress_response = client.get(f"/api/progress/{job_id}")
        progress = progress_response.get_json()
        if progress.get("status") in {"done", "error"}:
            break
        import time
        time.sleep(0.2)

    assert progress is not None
    assert progress["status"] == "done", progress
    assert "report" in progress
    report = progress["report"]
    assert report["status"] == "ok"
    assert report["source_file"].endswith("sample.docx")
    assert "citation_matches" in report
    assert report["citation_matches"][0]["citation"] == "[1]"
    assert report["citation_matches"][0]["matched_reference"] == 1


@patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False)
def test_upload_pdf_manuscript_starts_job_and_returns_report():
    client = app.test_client()
    pdf_bytes = _make_pdf_bytes(
        "This claim is supported by prior work [1].\n\nReferences\n[1] Smith A. Example Paper. 2020. https://doi.org/10.1000/example"
    )

    response = client.post(
        "/api/upload-manuscript",
        data={"file": (io.BytesIO(pdf_bytes), "sample.pdf")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert "job_id" in payload
    job_id = payload["job_id"]

    progress = None
    for _ in range(30):
        progress_response = client.get(f"/api/progress/{job_id}")
        progress = progress_response.get_json()
        if progress.get("status") in {"done", "error"}:
            break
        import time
        time.sleep(0.2)

    assert progress is not None
    assert progress["status"] == "done", progress
    report = progress["report"]
    assert report["status"] == "ok"
    assert report["source_file"].endswith("sample.pdf")
    assert report["citation_matches"][0]["citation"] == "[1]"


@patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False)
def test_upload_manuscript_with_author_year_citations_does_not_fail():
    client = app.test_client()
    docx_bytes = _make_docx_bytes(
        "Smith et al. (2020) showed this effect in teleost fish.\n\nReferences\nSmith A. Example Paper. 2020. https://doi.org/10.1000/example"
    )

    response = client.post(
        "/api/upload-manuscript",
        data={"file": (io.BytesIO(docx_bytes), "author_year_demo.docx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    payload = response.get_json()
    job_id = payload["job_id"]

    progress = None
    for _ in range(30):
        progress_response = client.get(f"/api/progress/{job_id}")
        progress = progress_response.get_json()
        if progress.get("status") in {"done", "error"}:
            break
        import time
        time.sleep(0.2)

    assert progress is not None
    assert progress["status"] == "done", progress
    assert progress["report"]["status"] == "ok"
    assert len(progress["report"]["sentences"]) >= 1


@patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False)
def test_upload_manuscript_with_non_bracket_author_year_citations_without_reference_header_is_not_rejected():
    client = app.test_client()
    docx_bytes = _make_docx_bytes(
        "The findings of Smith et al. (2020) align with the proposed mechanism.\n\nThis interpretation is consistent with prior work."
    )

    response = client.post(
        "/api/upload-manuscript",
        data={"file": (io.BytesIO(docx_bytes), "plain_author_year.docx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    payload = response.get_json()
    job_id = payload["job_id"]

    progress = None
    for _ in range(30):
        progress_response = client.get(f"/api/progress/{job_id}")
        progress = progress_response.get_json()
        if progress.get("status") in {"done", "error"}:
            break
        import time
        time.sleep(0.2)

    assert progress is not None
    assert progress["status"] == "done", progress
    assert progress["report"]["status"] == "ok"
    assert any("Smith" in sentence.get("text", "") for sentence in progress["report"]["sentences"]) or progress["report"]["citation_count"] >= 1


def test_orchestrator_match_entries_have_unique_case_keys():
    from orchestrator import runner

    sentences = [
        {"sentence_id": "s1", "id": "s1", "text": "Alpha [1] and beta [1]."},
        {"sentence_id": "s2", "id": "s2", "text": "Gamma [2]."},
    ]
    references = [
        {"number": 1, "raw": "Smith A. Example Paper. 2020."},
        {"number": 2, "raw": "Jones B. Other Paper. 2021."},
    ]

    matches = runner._match_citations_to_references(sentences, references)
    assert matches
    assert len({match.get("case_key") for match in matches}) == len(matches)

    enriched = runner._enrich_local_matches(matches, sentences, references)
    assert len({match.get("case_key") for match in enriched}) == len(enriched)


def test_run_orchestrator_keeps_local_matches_when_library_report_is_empty(tmp_path, monkeypatch):
    from orchestrator import runner

    docx_bytes = _make_docx_bytes(
        "Smith et al. (2020) showed this effect.\n\nReferences\nSmith A. Example Paper. 2020."
    )
    source_path = tmp_path / "sample.docx"
    source_path.write_bytes(docx_bytes)

    monkeypatch.setattr(
        runner,
        "_build_library_report",
        lambda *args, **kwargs: {"sentences": [], "citation_matches": [], "summary": {"total_sentences": 0, "total_checks": 0}},
    )

    result = runner.run_orchestrator(source_path, tmp_path / "out")
    report_path = tmp_path / "out" / "report.json"

    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(report["citation_matches"]) >= 1
    assert report["citation_count"] == len(report["citation_matches"])


@patch.dict(os.environ, {"OPENAI_API_KEY": "", "REFCHECK_ALLOW_OFFLINE_SIMULATION": "1"}, clear=False)
def test_run_orchestrator_distinguishes_file_success_from_judge_execution(tmp_path):
    from orchestrator import runner

    docx_bytes = _make_docx_bytes(
        "This claim is supported by prior work [1].\n\nReferences\n[1] Smith A. Example Paper. 2020. https://doi.org/10.1000/example"
    )
    source_path = tmp_path / "sample.docx"
    source_path.write_bytes(docx_bytes)

    result = runner.run_orchestrator(source_path, tmp_path / "out")
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    run_record = json.loads((tmp_path / "out" / "run_record.json").read_text(encoding="utf-8"))

    assert report["status"] == "ok"
    assert report["file_status"] == "ok"
    assert report["judge_executed"] is False
    assert report["judge_status"] == "simulated"
    assert report["judge_blocker"] == "explicit_opt_in"
    assert report["metadata_status"] in {"ok", "not_evaluated"}
    assert report["final_verdict_status"] == "not_applicable"
    assert report["simulation"] is True
    assert report["scientific_verdict"] is False

    assert run_record["file_status"] == "ok"
    assert run_record["judge_executed"] is False
    assert run_record["judge_status"] == "simulated"
    assert run_record["judge_blocker"] == "explicit_opt_in"
    assert run_record["scientific_verdict"] is False


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
def test_run_record_uses_real_judge_call_counts_and_prompt_identity(monkeypatch, tmp_path):
    from orchestrator import runner

    docx_bytes = _make_docx_bytes(
        "This claim is supported by prior work [1].\n\nReferences\n[1] Smith A. Example Paper. 2020. https://doi.org/10.1000/example"
    )
    source_path = tmp_path / "sample.docx"
    source_path.write_bytes(docx_bytes)

    monkeypatch.setattr(
        runner,
        "_build_library_report",
        lambda *args, **kwargs: {
            "judge_executed": True,
            "prompt_version": "v2.2.2",
            "prompt_hash": "abc123",
            "provider_attempts": 3,
            "call_log": [
                {"model": "gpt-5.6-luna"},
                {"model": "gpt-5.6-luna"},
                {"model": "gpt-5.6-sol"},
            ],
            "final_verdict": "supported",
            "scientific_verdict": True,
            "verdict_provenance": "judge",
            "sentences": [],
            "citation_matches": [],
            "summary": {"total_sentences": 0, "total_checks": 0},
        },
    )

    runner.run_orchestrator(source_path, tmp_path / "out")
    run_record = json.loads((tmp_path / "out" / "run_record.json").read_text(encoding="utf-8"))

    assert run_record["judge_ran"] is True
    assert run_record["judge_executed"] is True
    assert run_record["judge_prompt_version"] == "v2.2.2"
    assert run_record["judge_prompt_hash"] == "abc123"
    assert run_record["models_called"] == ["gpt-5.6-luna", "gpt-5.6-sol"]
    assert run_record["luna_calls"] == 2
    assert run_record["sol_calls"] == 1


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
def test_run_record_detects_real_judge_execution_without_top_level_flag(monkeypatch, tmp_path):
    from orchestrator import runner

    docx_bytes = _make_docx_bytes(
        "This claim is supported by prior work [1].\n\nReferences\n[1] Smith A. Example Paper. 2020. https://doi.org/10.1000/example"
    )
    source_path = tmp_path / "sample.docx"
    source_path.write_bytes(docx_bytes)

    monkeypatch.setattr(
        runner,
        "_build_library_report",
        lambda *args, **kwargs: {
            "prompt_version": "v2.2.2",
            "prompt_hash": "abc123",
            "provider_attempts": 2,
            "call_log": [
                {"model": "gpt-5.6-luna"},
                {"model": "gpt-5.6-sol"},
            ],
            "final_verdict": "unsupported",
            "scientific_verdict": True,
            "verdict_provenance": "judge",
            "sentences": [{
                "sentence_id": "s1",
                "pairs": [{
                    "ref_number": 1,
                    "status": "verdict",
                    "verdict": "unsupported",
                    "llm_call_attempted": True,
                }],
            }],
            "citation_matches": [],
            "summary": {"total_sentences": 1, "total_checks": 1},
        },
    )

    result = runner.run_orchestrator(source_path, tmp_path / "out")
    run_record = json.loads((tmp_path / "out" / "run_record.json").read_text(encoding="utf-8"))

    assert result["judge_executed"] is True
    assert run_record["judge_ran"] is True
    assert run_record["judge_executed"] is True
    assert run_record["judge_status"] == "executed"


def test_local_matches_keep_sentence_and_reference_context_for_rendering():
    from orchestrator import runner

    sentences = [{
        "sentence_id": "s1",
        "id": "s1",
        "text": "Smith et al. (2020) showed this effect.",
        "sentence_text": "Smith et al. (2020) showed this effect.",
    }]
    references = [{
        "number": 1,
        "raw": "Smith A. Example Paper. 2020. https://doi.org/10.1000/example",
        "doi": "10.1000/example",
        "source_url": "https://doi.org/10.1000/example",
    }]

    matches = runner._match_citations_to_references(sentences, references)
    enriched = runner._enrich_local_matches(matches, sentences, references)

    assert enriched[0]["sentence_text"] == "Smith et al. (2020) showed this effect."
    assert enriched[0]["claim"] == "Smith et al. (2020) showed this effect."
    assert enriched[0]["reference_text"] == "Smith A. Example Paper. 2020. https://doi.org/10.1000/example"
    assert enriched[0].get("source_excerpt") is None


def test_local_matches_keep_actual_source_excerpt_separate_from_reference_entry():
    from orchestrator import runner

    matches = [{
        "sentence_id": "s1",
        "citation": "[1]",
        "matched_reference": 1,
        "source_excerpt": "This is the actual source passage from the cited paper.",
        "status": "issue",
    }]
    sentences = [{"sentence_id": "s1", "text": "This claim cites a study [1]."}]
    references = [{
        "number": 1,
        "raw": "Smith A. Example Paper. 2020. https://doi.org/10.1000/example",
        "doi": "10.1000/example",
        "source_url": "https://doi.org/10.1000/example",
    }]

    enriched = runner._enrich_local_matches(matches, sentences, references)

    assert enriched[0]["reference_text"] == "Smith A. Example Paper. 2020. https://doi.org/10.1000/example"
    assert enriched[0]["source_excerpt"] == "This is the actual source passage from the cited paper."


def test_ensure_reference_text_for_matches_reconstructs_bibliography_from_reference_number():
    from orchestrator import runner

    matches = [{
        "sentence_id": "s1",
        "citation": "[1]",
        "matched_reference": 1,
        "status": "matched",
    }]
    references = [{
        "number": 1,
        "raw": "Smith A. Example Paper. 2020. https://doi.org/10.1000/example",
    }]

    fixed = runner._ensure_reference_text_for_matches(matches, references)

    assert fixed[0]["reference_text"] == "Smith A. Example Paper. 2020. https://doi.org/10.1000/example"


def test_local_matches_use_specific_issue_reason_instead_of_generic_match_text():
    from orchestrator import runner

    matches = [{
        "sentence_id": "s1",
        "citation": "[1]",
        "matched_reference": 1,
        "status": "issue",
        "error_type": "source_says_opposite",
        "match_rule": "source_says_opposite",
    }]
    sentences = [{"sentence_id": "s1", "text": "This claim contradicts the source [1]."}]
    references = [{"number": 1, "raw": "Smith A. Example Paper. 2020."}]

    enriched = runner._enrich_local_matches(matches, sentences, references)

    assert enriched[0]["reason"] == "The source says the opposite"
    assert enriched[0]["reason"] != "The manuscript citation was matched to the corresponding reference entry."


def test_report_text_result_keeps_detailed_observation_in_final_report_payload():
    from dataclasses import asdict

    from src.pipeline import PairResult

    pair = PairResult(
        sentence_id="s1",
        sentence_text="The mutation was associated with elevated cortisol [1].",
        ref_number=1,
        status="verdict",
        verdict="unsupported",
        explanation="The source reports the opposite effect.",
        report_text_result={
            "status": "ready",
            "reply": {
                "error_type": "source_says_opposite",
                "observation": "The source reports the opposite effect, so the manuscript sentence overstates what the paper actually concludes.",
                "evidence_passage": "**the source reports the opposite effect**",
            },
        },
    )

    payload = asdict(pair)

    assert payload["explanation"] == "The source reports the opposite effect."
    assert payload["observation"] == "The source reports the opposite effect, so the manuscript sentence overstates what the paper actually concludes."
    assert payload["reason"] == payload["observation"]
    assert payload["evidence_quote"] == "**the source reports the opposite effect**"
    assert payload["report_text_result"]["reply"]["observation"] == payload["observation"]


def test_report_text_result_overrides_judge_explanation_in_normalized_match_payload():
    from orchestrator import runner

    report = {
        "sentences": [{
            "sentence_id": "s1",
            "sentence_text": "The mutation was associated with elevated cortisol [1].",
            "pairs": [{
                "sentence_id": "s1",
                "sentence_text": "The mutation was associated with elevated cortisol [1].",
                "ref_number": 1,
                "status": "verdict",
                "verdict": "unsupported",
                "explanation": "The judge explanation copied from the vote.",
                "source_type": "full_text",
                "report_text_result": {
                    "status": "ready",
                    "reply": {
                        "error_type": "source_says_opposite",
                        "observation": "The source reports the opposite effect, so the sentence overstates the finding.",
                        "evidence_passage": "**the source reports the opposite effect**",
                    },
                },
            }],
        }],
        "summary": {"total_sentences": 1},
    }

    normalized = runner._normalize_library_report(report)
    match = normalized["citation_matches"][0]

    assert match["error_type"] == "source_says_opposite"
    assert match["observation"] == "The source reports the opposite effect, so the sentence overstates the finding."
    assert match["reason"] == match["observation"]
    assert match["source_excerpt"] == "**the source reports the opposite effect**"
    assert match["evidence_quote"] == "**the source reports the opposite effect**"


def _pair(ref_number, verdict, **extra):
    return {
        "sentence_id": "s1",
        "sentence_text": "Smith et al. (2020) showed this [1].",
        "ref_number": ref_number,
        "status": "verdict",
        "verdict": verdict,
        "source_type": "full_text",
        **extra,
    }


def test_uncertain_pair_is_not_labelled_as_an_issue_with_a_default_error_type():
    from orchestrator import runner

    report = {
        "sentences": [{"sentence_id": "s1", "sentence_text": "x [1].", "pairs": [_pair(1, "uncertain", secondary_family="none")]}],
        "summary": {},
    }

    normalized = runner._normalize_library_report(report)
    match = normalized["citation_matches"][0]

    assert match["status"] == "uncertain"
    assert match["error_type"] is None
    assert normalized["cases"] == []


def test_failed_report_text_reply_is_never_used_and_builds_no_case():
    from orchestrator import runner

    reply = {"error_type": "source_says_opposite", "claim_marked": "**x**", "evidence_passage": "**y**", "evidence_context": None, "observation": "bad"}
    pair = _pair(1, "unsupported", show_content=True, report_text_result={"status": "report_text_failed", "reply": reply})
    report = {"sentences": [{"sentence_id": "s1", "sentence_text": "x [1].", "pairs": [pair]}], "summary": {}}

    normalized = runner._normalize_library_report(report)

    assert normalized["cases"] == []
    assert normalized["citation_matches"][0]["claim_marked"] is None
    assert normalized["citation_matches"][0]["report_text_status"] == "report_text_failed"


def test_validated_report_text_reply_builds_a_complete_content_case_with_all_five_fields():
    from orchestrator import runner

    reply = {
        "error_type": "source_says_opposite",
        "claim_marked": "Smith **showed this** [1].",
        "evidence_passage": "The source **shows the opposite**.",
        "evidence_context": None,
        "observation": "The source reports the opposite effect.",
    }
    pair = _pair(
        1, "unsupported", show_content=True,
        evidence_blocks=[{"location": "body", "text": "The source shows the opposite."}],
        report_text_result={"status": "ready", "reply": reply},
    )
    report = {"sentences": [{"sentence_id": "s1", "sentence_text": "x [1].", "pairs": [pair]}], "summary": {}}
    references = [{"number": 1, "raw": "[1] Smith A. 2020. Example paper.", "doi": "10.1/x"}]

    normalized = runner._normalize_library_report(report, references)
    match = normalized["citation_matches"][0]
    case = normalized["cases"][0]

    assert (match["claim_marked"], match["evidence_passage"], match["evidence_context"]) == (
        reply["claim_marked"], reply["evidence_passage"], None,
    )
    assert case["reference_id"] == 1
    assert case["marker"] == "[1]"
    assert case["kind"] == "content"
    assert case["source_label"] == "Smith (2020)"
    assert case["error_type"] == "source_says_opposite"
    assert case["claim"] == reply["claim_marked"]
    assert case["evidence"] == reply["evidence_passage"]
    assert case["passage_origin"] == "full_text"
    assert case["observation"] == reply["observation"]
    assert case["doi"] == "10.1/x"
    assert "evidence_context" not in case
    assert "rewrite" not in case


def test_unsupported_pair_hidden_by_metadata_builds_no_case():
    from orchestrator import runner

    reply = {"error_type": "source_does_not_say", "claim_marked": "**x**", "evidence_passage": "**y**", "evidence_context": None, "observation": "o"}
    pair = _pair(1, "unsupported", show_content=False, report_text_result={"status": "ready", "reply": reply})
    report = {"sentences": [{"sentence_id": "s1", "sentence_text": "x [1].", "pairs": [pair]}], "summary": {}}

    assert runner._normalize_library_report(report)["cases"] == []


def test_normalize_library_report_keeps_real_error_type_from_json_report():
    from orchestrator import runner

    report = {
        "sentences": [{
            "sentence_id": "s1",
            "sentence_text": "Smith et al. (2020) showed this.",
            "pairs": [{
                "sentence_id": "s1",
                "sentence_text": "Smith et al. (2020) showed this.",
                "ref_number": 1,
                "status": "issue",
                "error_type": "source_says_opposite",
                "primary_family": "source_says_opposite",
                "explanation": "The source says the opposite",
                "source_type": "full_text",
            }],
        }],
        "summary": {"total_sentences": 1},
    }

    normalized = runner._normalize_library_report(report)
    match = normalized["citation_matches"][0]

    assert match["error_type"] == "source_says_opposite"
    assert match["primary_family"] == "source_says_opposite"
    assert match["reason"] == "The source says the opposite"


def test_local_matches_ignore_technical_failure_statuses_as_user_facing_labels():
    from orchestrator import runner

    matches = [{
        "sentence_id": "s1",
        "citation": "[1]",
        "matched_reference": 1,
        "status": "source_could_not_be_opened",
        "reason": "source could not be opened",
        "match_rule": "source_could_not_be_opened",
    }]
    sentences = [{"sentence_id": "s1", "text": "This claim cites a source [1]."}]
    references = [{"number": 1, "raw": "Smith A. Example Paper. 2020."}]

    enriched = runner._enrich_local_matches(matches, sentences, references)

    assert enriched[0]["reason"] in {"", "Citation issue"}
    assert "The citation was checked against the corresponding reference entry." not in enriched[0]["reason"]


def test_unsupported_match_without_specific_error_type_defaults_to_approved_content_category():
    from orchestrator import runner

    matches = [{
        "sentence_id": "s1",
        "citation": "[1]",
        "matched_reference": 1,
        "status": "issue",
        "reason": "The citation was checked against the corresponding reference entry.",
    }]
    sentences = [{"sentence_id": "s1", "text": "This claim is unsupported by the cited source [1]."}]
    references = [{"number": 1, "raw": "Smith A. Example Paper. 2020."}]

    enriched = runner._enrich_local_matches(matches, sentences, references)

    assert enriched[0]["error_type"] == "source_does_not_say"
    assert enriched[0]["reason"] == ""
    assert enriched[0]["observation"] == ""


def test_local_matches_ignore_generic_default_reason_when_specific_issue_exists():
    from orchestrator import runner

    matches = [{
        "sentence_id": "s1",
        "citation": "[1]",
        "matched_reference": 1,
        "status": "issue",
        "reason": "The citation was checked against the corresponding reference entry.",
        "error_type": "source_says_opposite",
        "match_rule": "source_says_opposite",
    }]
    sentences = [{"sentence_id": "s1", "text": "This claim contradicts the source [1]."}]
    references = [{"number": 1, "raw": "Smith A. Example Paper. 2020."}]

    enriched = runner._enrich_local_matches(matches, sentences, references)

    assert enriched[0]["reason"] == "The source says the opposite"
    assert enriched[0]["reason"] != "The citation was checked against the corresponding reference entry."


def test_normalize_library_report_uses_the_real_manuscript_citation_token():
    from orchestrator import runner

    report = {
        "sentences": [
            {
                "sentence_id": "s1",
                "sentence_text": "Johnston et al. (2011) reported a similar signal.",
                "pairs": [
                    {
                        "sentence_id": "s1",
                        "sentence_text": "Johnston et al. (2011) reported a similar signal.",
                        "ref_number": 1109473,
                        "status": "verdict",
                        "verdict": "supported",
                        "explanation": "The claim is supported by the cited source.",
                        "source_type": "full_text",
                    }
                ],
            }
        ],
        "summary": {"total_sentences": 1},
    }

    normalized = runner._normalize_library_report(report)
    match = normalized["citation_matches"][0]
    assert match["citation"] == "Johnston et al. (2011)"
    assert match["matched_reference"] == 1109473
    assert match["sentence_text"] == "Johnston et al. (2011) reported a similar signal."
    assert match["claim"] == "Johnston et al. (2011) reported a similar signal."


def test_normalize_library_report_keeps_llm_verdict_and_issue_rows():
    from orchestrator import runner

    report = {
        "sentences": [
            {
                "sentence_id": "s1",
                "sentence_text": "Smith et al. (2020) showed this.",
                "pairs": [
                    {
                        "sentence_id": "s1",
                        "sentence_text": "Smith et al. (2020) showed this.",
                        "ref_number": 1,
                        "status": "verdict",
                        "verdict": "unsupported",
                        "explanation": "The claim is not supported by the cited source.",
                        "source_type": "full_text",
                    },
                    {
                        "sentence_id": "s1",
                        "sentence_text": "Smith et al. (2020) showed this.",
                        "ref_number": 2,
                        "status": "technical_failure",
                        "error_message": "Content fetch failed.",
                        "source_type": "abstract",
                    },
                ],
            }
        ],
        "summary": {"total_sentences": 1},
    }

    normalized = runner._normalize_library_report(report)
    assert len(normalized["citation_matches"]) == 2
    assert any(item["matched_reference"] == 1 and item["status"] == "issue" for item in normalized["citation_matches"])
    assert any(item["matched_reference"] == 2 and item["status"] == "technical_failure" for item in normalized["citation_matches"])


def test_normalize_library_report_copies_real_evidence_into_source_excerpt():
    from orchestrator import runner

    report = {
        "sentences": [
            {
                "sentence_id": "s1",
                "sentence_text": "Smith et al. (2020) showed this.",
                "pairs": [
                    {
                        "sentence_id": "s1",
                        "sentence_text": "Smith et al. (2020) showed this.",
                        "ref_number": 1,
                        "status": "verdict",
                        "verdict": "unsupported",
                        "explanation": "The claim is not supported by the cited source.",
                        "source_type": "full_text",
                        "evidence_quote": "This is the actual source passage from the cited paper.",
                    }
                ],
            }
        ],
        "summary": {"total_sentences": 1},
    }

    normalized = runner._normalize_library_report(report)
    match = normalized["citation_matches"][0]
    assert match["source_excerpt"] == "This is the actual source passage from the cited paper."
    assert match["evidence_quote"] == "This is the actual source passage from the cited paper."


def test_normalize_library_report_prefers_live_judge_evidence_quotes_over_abstract_excerpt():
    from orchestrator import runner

    report = {
        "sentences": [
            {
                "sentence_id": "s1",
                "sentence_text": "Smith et al. (2020) showed this.",
                "pairs": [
                    {
                        "sentence_id": "s1",
                        "sentence_text": "Smith et al. (2020) showed this.",
                        "ref_number": 1,
                        "status": "verdict",
                        "verdict": "unsupported",
                        "explanation": "The claim is not supported by the cited source.",
                        "source_type": "abstract",
                        "source_excerpt": "This is the opening of the abstract and not the actual judge quote.",
                        "evidence_quotes": [
                            "This is the actual source passage from the cited paper.",
                            "And this is a second exact quote from the judge.",
                        ],
                    }
                ],
            }
        ],
        "summary": {"total_sentences": 1},
    }

    normalized = runner._normalize_library_report(report)
    match = normalized["citation_matches"][0]
    assert match["source_excerpt"] == "This is the actual source passage from the cited paper."
    assert match["evidence_quote"] == "This is the actual source passage from the cited paper."
    assert match["evidence_quotes"] == [
        "This is the actual source passage from the cited paper.",
        "And this is a second exact quote from the judge.",
    ]


def test_normalize_library_report_preserves_specific_source_failure_statuses():
    from orchestrator import runner

    report = {
        "sentences": [
            {
                "sentence_id": "s1",
                "sentence_text": "Smith et al. (2020) showed this.",
                "pairs": [
                    {
                        "sentence_id": "s1",
                        "sentence_text": "Smith et al. (2020) showed this.",
                        "ref_number": 1,
                        "status": "source_could_not_be_opened",
                        "error_message": "No source reachable.",
                        "source_type": "abstract",
                    }
                ],
            }
        ],
        "summary": {"total_sentences": 1},
    }

    normalized = runner._normalize_library_report(report)
    match = normalized["citation_matches"][0]
    assert match["status"] == "source_could_not_be_opened"
    assert match["match_rule"] == "source_could_not_be_opened"
    assert match["kind"] == "content"


def test_template_prefers_specific_reason_over_generic_issue_label():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    reason_block = html.split("function getMatchReasonKey(match)", 1)[1].split("function getObservationText", 1)[0]

    assert "match && match.status" in reason_block
    assert "match && match.match_rule" in reason_block
    assert reason_block.index("match && match.status") < reason_block.index("match && match.match_rule")
    assert reason_block.index("match && match.match_rule") < reason_block.index("match && match.primary_family")


def test_template_filters_uncertain_and_generic_fallback_labels():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    normalise_block = html.split("function normaliseMatchLabel(match)", 1)[1].split("function getMatchPillClass", 1)[0]
    reason_block = html.split("function getMatchReason(match)", 1)[1].split("function getMatchClaim", 1)[0]

    assert "'uncertain'" in html
    assert "'citation_issue'" in html
    assert "'unsupported_citation'" in html
    assert "return ''" in normalise_block
    assert "['uncertain', 'unsupported', 'issue', 'citation_issue', 'unsupported_citation'" in reason_block
    assert "'Citation issue'" not in normalise_block
    assert "'Unsupported citation'" not in normalise_block


def test_template_uses_view_source_anchor_without_arrow_suffix():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert 'View source</a>' in html
    assert 'View source ↗' not in html


def test_template_excludes_supported_cases_from_visible_issue_counts():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert 'function isSupportedCase(match)' in html
    assert 'if (isSupportedCase(match)) return false;' in html


def test_template_counts_come_from_report_cases():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    block = html.split("function summarizeReport(report)", 1)[1].split("function renderResult", 1)[0]

    assert "Array.isArray(report && report.cases)" in block
    assert "cases.filter(function (c) { return c.kind !== 'metadata'; })" in block
    assert "cases.filter(function (c) { return c.kind === 'metadata'; })" in block
    assert "citationErrors: distinctReferenceCount(contentCases)" in block
    assert "nContent.textContent = String(summary.citationErrors);" in block
    assert "nMeta.textContent = String(summary.metadataProblems);" in block
    assert "Citation errors: " in block
    assert "Require verification: " in block
    assert "Metadata problems: " in block
    assert "count_as_reference !== false" in html
    assert "citation_matches" not in block


def test_template_lists_report_cases_and_colours_metadata_pills_amber():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    block = html.split("function renderAnalysisDesign(report, fileName)", 1)[1].split("function renderResult", 1)[0]
    card = html.split("function renderCaseCard(c, index)", 1)[1].split("function distinctReferenceCount", 1)[0]

    assert "cases.map(renderCaseCard).join('')" in block
    assert "kind === 'metadata' ? 'pill-amber' : 'pill-red'" in card
    assert ".pill-amber{color:var(--amber);" in html
    assert "--amber:#C47700;" in html


def test_template_escapes_case_text_before_rendering():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    body = html.split("function renderCaseBody(c)", 1)[1].split("function renderCaseCard", 1)[0]

    assert "function escHtml(value)" in html
    assert "markedHtml(c.claim, 'claim')" in body
    assert "escHtml(c.reference)" in body
    assert "'<p>' + c.claim" not in body


def test_template_keeps_source_passage_and_reference_entry_separate():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert 'getSourcePassage(match)' in html
    assert 'getReferenceText(match)' in html
    assert 'What the source says' in html
    assert 'Cited source' in html
    assert 'match.reason' not in html.split('function getReferenceText(match)', 1)[1].split('function getSourceExcerpt', 1)[0]


def test_template_prefers_detailed_judge_explanation_over_short_error_label():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert "const detailCandidates = [" in html
    assert "match.explanation" in html
    assert "const labelSource = match.error_type" in html
    assert "return ''" in html.split("function getMatchReason(match)", 1)[1].split("function getMatchClaim", 1)[0]


def test_template_keeps_uncertain_cases_out_of_confirmed_error_counts_and_uses_evidence_quote_for_source_passage():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert "match.verdict === 'uncertain'" in html
    assert "getSourcePassage(match)" in html
    assert "match.evidence_quote" in html
    assert "match.source_excerpt" in html


def test_template_uses_exact_evidence_quotes_without_assessment_label():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert "const quoteList = Array.isArray(match.evidence_quotes)" in html
    assert "match.evidence_quotes" in html
    assert "Assessment:" not in html
    assert "<b>What the source says</b>" in html


def test_template_ignores_generic_error_labels_in_observation_content():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert "isCanonicalIssueLabelText" in html
    assert "The source does not say that" in html
    assert "return ''" in html.split("function getMatchReason(match)", 1)[1].split("function getMatchClaim", 1)[0]


def test_template_ignores_generic_match_rule_when_rendering_error_labels():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert "isGenericMatchRule" in html
    assert "match_rule" in html
    assert "!isGenericMatchRule(match.match_rule)" in html


def test_template_prefers_analysis_error_type_over_generic_source_statuses():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert "source_could_not_be_opened" in html
    assert "source_obtained" in html
    assert "genericStatusValues" in html
    assert "match.reason" in html
    assert "match.explanation" in html


def test_template_contains_exact_error_type_taxonomy_and_bibliography_language():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert "The source says the opposite" in html
    assert "The study did not do that" in html
    assert "Data does not match the study" in html
    assert "Inflated figure" in html
    assert "Figure taken out of context" in html
    assert "One measure confused with another" in html
    assert "Incorrect reference details" in html
    assert "Fabricated reference" in html
    assert "reference list/bibliography" in html or "bibliographic entry was checked field by field against the official record in Crossref" in html


def test_report_text_prompt_lists_only_approved_content_error_types():
    prompt = Path("src/prompts/report_text_v2.txt").read_text(encoding="utf-8")
    valid_block = prompt.split("Valid error keys for this stage are exactly these eight:", 1)[1].split("Never output", 1)[0]
    for token in [
        "source_says_opposite",
        "study_did_not_do_that",
        "data_mismatch",
        "inflated_figure",
        "figure_out_of_context",
        "measure_confused",
        "source_does_not_say",
        "overstated_certainty",
    ]:
        assert token in prompt
        assert token in valid_block
    for forbidden in [
        "source_could_not_be_opened",
        "source_abstained",
        "hybrid live judge",
        "cascade returned an uncertain verdict",
        "technical_failure",
    ]:
        assert forbidden not in valid_block.lower()
    assert "Never output a technical-failure label" in prompt
    assert "Valid error keys for this stage are exactly these eight" in prompt
