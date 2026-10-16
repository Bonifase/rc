import io
import json
import zipfile
from pathlib import Path

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


def test_template_uses_view_source_anchor_without_arrow_suffix():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert 'View source</a>' in html
    assert 'View source ↗' not in html


def test_template_counts_unique_affected_references_for_content_summary():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert 'const uniqueContentRefs = new Set(contentMatches.map(function (match) { return String(match.matched_reference ?? match.reference_id); }));' in html
    assert 'const errorCount = uniqueContentRefs.size;' in html
    assert 'errorText = \'<strong>\' + errorCount + \' contain errors.</strong>\';' in html


def test_template_deduplicates_visible_cases_for_match_status_lists():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert 'function dedupeVisibleCases(matches)' in html
    assert 'const visibleMatches = dedupeVisibleCases(matches);' in html
    assert 'const rows = visibleMatches.length ? visibleMatches.map(function (match) {' in html


def test_template_keeps_source_passage_and_reference_entry_separate():
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert 'getSourcePassage(match)' in html
    assert 'getReferenceText(match)' in html
    assert 'What the source says' in html
    assert 'Cited source' in html


def test_template_ignores_generic_match_rule_when_rendering_error_labels():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert "isGenericMatchRule" in html
    assert "match_rule" in html
    assert "!isGenericMatchRule(match.match_rule)" in html


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
