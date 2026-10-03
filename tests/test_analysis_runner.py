from unittest.mock import patch

from src.analysis_runner import run_content_and_metadata, EMPTY_METADATA_REPORT
from src.pipeline import PairResult

TEST_EMAIL = "test@example.com"


def _fake_pair_results():
    return [
        PairResult(
            sentence_id="s1", sentence_text="A claim [1].", ref_number=1, doi="10.1/x",
            status="verdict", verdict="supported", source_type="full_text", citation_context="sole",
            llm_call_attempted=True,
        )
    ]


@patch("src.analysis_runner.build_metadata_report")
@patch("src.analysis_runner.process_sentences")
def test_returns_both_content_and_metadata_results(mock_process, mock_metadata):
    mock_process.return_value = _fake_pair_results()
    mock_metadata.return_value = {"bibliography_status": [{"reference_id": 1}], "document_level_findings": [], "not_evaluated_checks": []}

    sentences = [{"id": "s1", "text": "A claim [1]."}]
    reference_list = {1: "10.1/x"}
    reference_entries = [{"number": 1, "doi": "10.1/x"}]

    pair_results, metadata_report = run_content_and_metadata(
        sentences, reference_list, reference_entries, "", TEST_EMAIL
    )

    assert pair_results == _fake_pair_results()
    assert metadata_report["bibliography_status"] == [{"reference_id": 1}]


@patch("src.analysis_runner.build_metadata_report")
@patch("src.analysis_runner.process_sentences")
def test_metadata_failure_still_returns_content_results(mock_process, mock_metadata):
    mock_process.return_value = _fake_pair_results()
    mock_metadata.side_effect = RuntimeError("Crossref is down")

    pair_results, metadata_report = run_content_and_metadata(
        [{"id": "s1", "text": "A claim [1]."}], {1: "10.1/x"}, [{"number": 1, "doi": "10.1/x"}], "", TEST_EMAIL
    )

    assert pair_results == _fake_pair_results()
    assert metadata_report["bibliography_status"] == []
    assert "Crossref is down" in metadata_report["error"]


@patch("src.analysis_runner.build_metadata_report")
@patch("src.analysis_runner.process_sentences")
def test_content_failure_raises_not_swallowed(mock_process, mock_metadata):
    mock_process.side_effect = RuntimeError("content pipeline exploded")
    mock_metadata.return_value = EMPTY_METADATA_REPORT

    try:
        run_content_and_metadata(
            [{"id": "s1", "text": "A claim [1]."}], {1: "10.1/x"}, [{"number": 1, "doi": "10.1/x"}], "", TEST_EMAIL
        )
        assert False, "expected RuntimeError to propagate"
    except RuntimeError as exc:
        assert "content pipeline exploded" in str(exc)


@patch("src.analysis_runner.build_metadata_report")
@patch("src.analysis_runner.process_sentences")
def test_reference_entries_with_raw_text_reach_metadata_module(mock_process, mock_metadata):
    mock_process.return_value = []
    mock_metadata.return_value = EMPTY_METADATA_REPORT

    reference_entries = [{"number": 1, "doi": "10.1/x", "raw": "Smith, J. (2020). A title. Journal, 1, 1-2."}]
    run_content_and_metadata([], {1: "10.1/x"}, reference_entries, "body text", TEST_EMAIL)

    call_args = mock_metadata.call_args
    doc_arg = call_args.args[0]
    references_arg = call_args.args[1]
    assert doc_arg.body_text == "body text"
    assert references_arg[0].journal == "Journal"
