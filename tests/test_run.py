import json
import pytest
from pathlib import Path

from run import load_input


def test_load_input_valid_file(tmp_path):
    data = {
        "reference_list": [{"number": 1, "doi": "10.1/x"}],
        "sentences": [{"id": "s1", "text": "A claim [1]."}],
    }
    file_path = tmp_path / "input.json"
    file_path.write_text(json.dumps(data))

    reference_list, sentences = load_input(file_path)

    assert reference_list == {1: {"number": 1, "doi": "10.1/x"}}
    assert sentences == [{"id": "s1", "text": "A claim [1]."}]


def test_load_input_accepts_identifier_free_reference_with_search_keys(tmp_path):
    data = {
        "reference_list": [{
            "number": 1,
            "title": "A paper without an identifier",
            "first_author": "Smith",
            "publication_year": 2020,
        }],
        "sentences": [{"id": "s1", "text": "A claim [1]."}],
    }
    file_path = tmp_path / "input.json"
    file_path.write_text(json.dumps(data))

    reference_list, sentences = load_input(file_path)

    assert reference_list == {1: {
        "number": 1,
        "title": "A paper without an identifier",
        "first_author": "Smith",
        "publication_year": 2020,
    }}


def test_load_input_missing_top_level_key_raises(tmp_path):
    data = {"reference_list": []}  # missing "sentences"
    file_path = tmp_path / "input.json"
    file_path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="reference_list.*sentences"):
        load_input(file_path)


def test_load_input_reference_entry_missing_doi_raises(tmp_path):
    data = {"reference_list": [{"number": 1}], "sentences": []}
    file_path = tmp_path / "input.json"
    file_path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="doi"):
        load_input(file_path)


def test_load_input_reference_entry_with_raw_needs_no_doi(tmp_path):
    data = {
        "reference_list": [{
            "number": 1,
            "raw": "[1] Smith, J. (2020). No DOIs here. Journal of Missing Identifiers, 2, 10-20.",
        }],
        "sentences": [],
    }
    file_path = tmp_path / "input.json"
    file_path.write_text(json.dumps(data))

    reference_list, _ = load_input(file_path)

    assert reference_list[1]["raw"].startswith("[1] Smith")


def test_load_input_sentence_missing_text_raises(tmp_path):
    data = {"reference_list": [], "sentences": [{"id": "s1"}]}
    file_path = tmp_path / "input.json"
    file_path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="text"):
        load_input(file_path)


def test_load_input_real_sample_file_is_valid():
    reference_list, sentences = load_input(Path("data/sample_input.json"))

    assert len(reference_list) >= 1
    assert all(isinstance(k, int) for k in reference_list.keys())
    assert len(sentences) >= 1
    for sentence in sentences:
        assert "id" in sentence and "text" in sentence


# ---------------------------------------------------------------------------
# main() end-to-end -- confirms the metadata module's output is merged into
# the written report, not just the content-pipeline's sentences/summary.
# ---------------------------------------------------------------------------

def test_main_writes_report_including_metadata(tmp_path, monkeypatch, capsys):
    import sys
    import run as run_module
    from src.pipeline import PairResult

    input_data = {
        "reference_list": [{"number": 1, "doi": "10.1/x"}],
        "sentences": [{"id": "s1", "text": "A claim [1]."}],
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(input_data))
    output_path = tmp_path / "report.json"

    fake_metadata_report = {
        "bibliography_status": [{
            "reference_id": 1, "status": "well_cited", "usable_identifier": "10.1/x",
            "findings": [], "used_in_sentences": ["s1"], "blocks_phase_2": False,
        }],
        "document_level_findings": [],
        "not_evaluated_checks": [{"check_id": 14, "component": "screening_criterion", "reason": "stub"}],
    }
    fake_pair_results = [
        PairResult(
            sentence_id="s1", sentence_text="A claim [1].", ref_number=1, doi="10.1/x",
            status="verdict", verdict="supported", source_type="full_text", citation_context="sole",
            llm_call_attempted=True,
        )
    ]

    monkeypatch.setattr(run_module, "require_contact_email", lambda: "test@example.com")
    monkeypatch.setattr(run_module, "run_content_and_metadata", lambda *a, **k: (fake_pair_results, fake_metadata_report))
    monkeypatch.setattr(sys, "argv", ["run.py", "--input", str(input_path), "--output", str(output_path)])

    run_module.main()

    written = json.loads(output_path.read_text())
    assert written["sentences"][0]["verdict"] == "supported"
    assert written["bibliography_status"] == fake_metadata_report["bibliography_status"]
    assert any(c["check_id"] == 14 for c in written["not_evaluated_checks"])

    captured = capsys.readouterr()
    assert "Reference metadata status" in captured.out
    assert "well_cited: 1" in captured.out


def test_main_surfaces_metadata_error_as_warning_without_failing(tmp_path, monkeypatch, capsys):
    import sys
    import run as run_module
    from src.pipeline import PairResult

    input_data = {
        "reference_list": [{"number": 1, "doi": "10.1/x"}],
        "sentences": [{"id": "s1", "text": "A claim [1]."}],
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(input_data))
    output_path = tmp_path / "report.json"

    fake_pair_results = [
        PairResult(
            sentence_id="s1", sentence_text="A claim [1].", ref_number=1, doi="10.1/x",
            status="verdict", verdict="supported", source_type="full_text", citation_context="sole",
            llm_call_attempted=True,
        )
    ]
    fake_metadata_report = {
        "bibliography_status": [], "document_level_findings": [], "not_evaluated_checks": [],
        "error": "Metadata module failed: boom",
    }

    monkeypatch.setattr(run_module, "require_contact_email", lambda: "test@example.com")
    monkeypatch.setattr(run_module, "run_content_and_metadata", lambda *a, **k: (fake_pair_results, fake_metadata_report))
    monkeypatch.setattr(sys, "argv", ["run.py", "--input", str(input_path), "--output", str(output_path)])

    run_module.main()  # must not raise -- content results still get written

    written = json.loads(output_path.read_text())
    assert written["sentences"][0]["verdict"] == "supported"
    assert written["metadata_error"] == "Metadata module failed: boom"

    captured = capsys.readouterr()
    assert "Warning: Metadata module failed: boom" in captured.err
