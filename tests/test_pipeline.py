import os
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src.analysis_runner import run_content_and_metadata
from src.content_fetcher import ContentResult
from src.crossref import CrossrefResult
from src.llm_wrapper import VerdictResult
from src.pipeline import (PairResult, process_pair, process_sentence,
                          process_sentences)
from src.pmc_id_converter import PmcIdResult
from src.progress import ProgressTracker

TEST_EMAIL = "test@example.com"


# ---------------------------------------------------------------------------
# process_pair -- every distinct outcome, none collapsed into another
# ---------------------------------------------------------------------------

@patch("src.pipeline.resolve_doi")
def test_doi_not_found_is_its_own_status_not_technical_failure(mock_resolve):
    mock_resolve.return_value = CrossrefResult(doi="10.1/x", status="not_found")

    result = process_pair("s1", "A claim [1].", 1, "10.1/x", "sole", TEST_EMAIL)

    assert result.status == "doi_not_found"
    assert result.llm_call_attempted is False


@patch("src.pipeline.resolve_doi")
def test_crossref_transient_error_is_technical_failure_not_not_found(mock_resolve):
    # Guard 2: a failed call must never look like "this reference doesn't exist"
    mock_resolve.return_value = CrossrefResult(doi="10.1/x", status="error", error_message="timeout")

    result = process_pair("s1", "A claim [1].", 1, "10.1/x", "sole", TEST_EMAIL)

    assert result.status == "technical_failure"
    assert result.status != "doi_not_found"
    assert "Crossref lookup failed" in result.error_message


@patch("src.pipeline.fetch_source_content")
@patch("src.pipeline.convert_doi")
@patch("src.pipeline.resolve_doi")
def test_not_indexed_is_its_own_status(mock_resolve, mock_convert, mock_fetch):
    mock_resolve.return_value = CrossrefResult(doi="10.1/x", status="resolved", title="A Paper")
    mock_convert.return_value = PmcIdResult(doi="10.1/x", status="not_indexed")
    # Extended retrieval runs for a not-indexed record; when it finds no
    # reachable content the pipeline still reports the not-indexed status.
    mock_fetch.return_value = ContentResult(status="unavailable")

    result = process_pair("s1", "A claim [1].", 1, "10.1/x", "sole", TEST_EMAIL)

    assert result.status == "source_not_indexed"
    mock_fetch.assert_called_once()


@patch("src.pipeline.fetch_source_content")
@patch("src.pipeline.convert_doi")
@patch("src.pipeline.resolve_doi")
def test_no_content_available_is_source_could_not_be_opened(mock_resolve, mock_convert, mock_fetch):
    mock_resolve.return_value = CrossrefResult(doi="10.1/x", status="resolved", title="A Paper")
    mock_convert.return_value = PmcIdResult(doi="10.1/x", status="converted", pmid="123", pmcid=None)
    mock_fetch.return_value = ContentResult(status="unavailable")

    result = process_pair("s1", "A claim [1].", 1, "10.1/x", "sole", TEST_EMAIL)

    assert result.status == "source_could_not_be_opened"


@patch("src.pipeline.fetch_source_content")
@patch("src.pipeline.convert_doi")
@patch("src.pipeline.resolve_doi")
def test_unusable_abstract_is_source_could_not_be_opened_and_skips_llm(mock_resolve, mock_convert, mock_fetch):
    mock_resolve.return_value = CrossrefResult(doi="10.1/x", status="resolved", title="A Paper")
    mock_convert.return_value = PmcIdResult(doi="10.1/x", status="converted", pmid="123", pmcid=None)
    mock_fetch.return_value = ContentResult(status="abstract_only", source="pubmed", text="N/A")

    with patch("src.pipeline.get_verdict") as mock_llm:
        result = process_pair("s1", "A claim [1].", 1, "10.1/x", "sole", TEST_EMAIL)
        mock_llm.assert_not_called()

    assert result.status == "source_could_not_be_opened"


@patch("src.pipeline.get_verdict")
@patch("src.pipeline.fetch_source_content")
@patch("src.pipeline.convert_doi")
@patch("src.pipeline.resolve_doi")
def test_llm_system_error_is_technical_failure(mock_resolve, mock_convert, mock_fetch, mock_llm):
    mock_resolve.return_value = CrossrefResult(doi="10.1/x", status="resolved", title="A Paper")
    mock_convert.return_value = PmcIdResult(doi="10.1/x", status="converted", pmid="123", pmcid=None)
    mock_fetch.return_value = ContentResult(status="abstract_only", source="pubmed", text="A usable abstract with enough length to pass the guard check easily.")
    mock_llm.return_value = VerdictResult(status="system_error", error_message="JSON parse failed x3")

    result = process_pair("s1", "A claim [1].", 1, "10.1/x", "sole", TEST_EMAIL)

    assert result.status == "technical_failure"
    assert "LLM comparison failed" in result.error_message
    assert result.llm_call_attempted is True  # it DID reach the LLM, just failed there


@patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False)
@patch("src.pipeline.log_llm_call")
@patch("src.pipeline.get_verdict")
@patch("src.pipeline.fetch_source_content")
@patch("src.pipeline.convert_doi")
@patch("src.pipeline.resolve_doi")
def test_no_api_key_uses_local_fallback_before_the_llm_step(
    mock_resolve, mock_convert, mock_fetch, mock_llm, mock_log
):
    mock_resolve.return_value = CrossrefResult(doi="10.1/x", status="resolved", title="A Paper")
    mock_convert.return_value = PmcIdResult(doi="10.1/x", status="converted", pmid="123", pmcid="PMC456")
    mock_fetch.return_value = ContentResult(status="full_text", source="pmc", text="the body text")

    result = process_pair("s1", "A claim [1].", 1, "10.1/x", "sole", TEST_EMAIL)

    assert result.status == "source_could_not_be_opened"
    assert result.llm_call_attempted is False
    assert "local parsing fallback" in result.error_message
    mock_llm.assert_not_called()
    mock_resolve.assert_not_called()
    mock_convert.assert_not_called()
    mock_fetch.assert_not_called()


@patch("src.pipeline.log_llm_call")
@patch("src.pipeline.get_verdict")
@patch("src.pipeline.fetch_source_content")
@patch("src.pipeline.convert_doi")
@patch("src.pipeline.resolve_doi")
def test_full_success_produces_verdict_status_and_logs_the_call(
    mock_resolve, mock_convert, mock_fetch, mock_llm, mock_log
):
    mock_resolve.return_value = CrossrefResult(doi="10.1/x", status="resolved", title="A Paper")
    mock_convert.return_value = PmcIdResult(doi="10.1/x", status="converted", pmid="123", pmcid="PMC456")
    mock_fetch.return_value = ContentResult(status="full_text", source="pmc", text="the body text")
    mock_llm.return_value = VerdictResult(
        status="verdict", verdict="supported", assertions_checked="the whole claim",
        primary_family=None, secondary_family="none", evidence_quote="the body text",
        omitted_contradicting_evidence="", source_attribution="own_finding",
        explanation="Supported.", quote_guard_triggered=False,
    )

    result = process_pair("s1", "A claim [1].", 1, "10.1/x", "sole", TEST_EMAIL)

    assert result.status == "verdict"
    assert result.verdict == "supported"
    assert result.source_type == "full_text"
    assert result.citation_context == "sole"
    assert result.llm_call_attempted is True
    mock_log.assert_called_once()


@patch("src.pipeline.log_llm_call")
@patch("src.pipeline.get_verdict")
@patch("src.pipeline.fetch_source_content")
@patch("src.pipeline.convert_doi")
@patch("src.pipeline.resolve_doi")
def test_llm_call_duration_is_logged(mock_resolve, mock_convert, mock_fetch, mock_llm, mock_log):
    import time as time_module

    mock_resolve.return_value = CrossrefResult(doi="10.1/x", status="resolved", title="A Paper")
    mock_convert.return_value = PmcIdResult(doi="10.1/x", status="converted", pmid="123", pmcid="PMC456")
    mock_fetch.return_value = ContentResult(status="full_text", source="pmc", text="the body text")

    def _slow_verdict(*args, **kwargs):
        # Sleep well above the assertion below so the measured duration stays
        # >= 0.05 even on Windows hosts whose monotonic clock has coarse
        # (15.6 ms) resolution and can quantize a short sleep downward.
        time_module.sleep(0.1)
        return VerdictResult(
            status="verdict", verdict="supported", assertions_checked="the whole claim",
            primary_family=None, secondary_family="none", evidence_quote="the body text",
            omitted_contradicting_evidence="", source_attribution="own_finding",
            explanation="Supported.", quote_guard_triggered=False,
        )
    mock_llm.side_effect = _slow_verdict

    process_pair("s1", "A claim [1].", 1, "10.1/x", "sole", TEST_EMAIL)

    _, kwargs = mock_log.call_args
    assert "duration_seconds" in kwargs
    assert kwargs["duration_seconds"] >= 0.05


@patch("src.pipeline.maybe_run_report_text_stage")
@patch("src.pipeline.log_llm_call")
@patch("src.pipeline.get_verdict")
@patch("src.pipeline.fetch_source_content")
@patch("src.pipeline.convert_doi")
@patch("src.pipeline.resolve_doi")
def test_report_text_input_uses_frozen_evidence_and_unsupported_sol_votes(
    mock_resolve, mock_convert, mock_fetch, mock_llm, mock_log, mock_stage
):
    import json

    body = "Alpha was measured. GR mutants were hypercortisolemic. Beta was not measured."
    mock_resolve.return_value = CrossrefResult(doi="10.1/x", status="resolved", title="A Paper")
    mock_convert.return_value = PmcIdResult(doi="10.1/x", status="converted", pmid="1", pmcid="PMC1")
    mock_fetch.return_value = ContentResult(status="full_text", source="pmc", text=body)

    def sol(slot, model, verdict, quotes, explanation, valid=True):
        return {
            "model": model, "vote_slot": slot, "verdict": verdict,
            "validation_status": "valid" if valid else "failed",
            "vote_response": {"evidence_quotes": quotes, "explanation": explanation},
        }

    cascade = {
        "final_verdict": "unsupported",
        "explanation": "final",
        "call_log": [
            sol(1, "gpt-5.6-luna", "unsupported", ["Luna quote"], "luna says"),
            sol(1, "gpt-5.6-sol", "unsupported", ["Alpha was measured."], "first"),
            sol(2, "gpt-5.6-sol", "supported", ["Beta was not measured."], "dissent"),
            sol(3, "gpt-5.6-sol", "unsupported", ["Alpha was measured.", "GR mutants were hypercortisolemic."], "second"),
            sol(4, "gpt-5.6-sol", "unsupported", ["bad"], "invalid", valid=False),
        ],
    }
    mock_llm.return_value = VerdictResult(
        status="verdict", verdict="unsupported", assertions_checked="x",
        primary_family=None, secondary_family="none", evidence_quote="Alpha was measured.",
        omitted_contradicting_evidence="", source_attribution="own_finding",
        explanation="final", quote_guard_triggered=False, raw_response=json.dumps(cascade),
    )
    mock_stage.return_value = {"status": "ready", "reply": {}}

    pair = process_pair("s1", "A claim [1].", 1, "10.1/x", "sole", TEST_EMAIL)
    mock_stage.assert_not_called()

    from src.pipeline import _apply_report_text_gate
    _apply_report_text_gate([pair])

    kwargs = mock_stage.call_args.kwargs
    assert kwargs["evidence"] == [{"location": "body", "text": body}]
    assert kwargs["judge_quotes"] == ["Alpha was measured.", "GR mutants were hypercortisolemic."]
    assert kwargs["judge_explanations"] == ["first", "second"]


def test_gate_fails_closed_without_frozen_evidence():
    from src.pipeline import _apply_report_text_gate

    pair = PairResult(
        sentence_id="s1", sentence_text="A claim [1].", ref_number=1, status="verdict",
        verdict="unsupported", source_type="full_text", citation_context="sole", show_content=True,
    )
    _apply_report_text_gate([pair])

    assert pair.report_text_result["status"] == "report_text_failed"


# ---------------------------------------------------------------------------
# process_sentence -- marker parsing, citation_context derivation, ref-list lookup
# ---------------------------------------------------------------------------

def test_sentence_with_no_citations_returns_single_placeholder():
    results = process_sentence("s1", "A sentence with no citations at all.", {}, TEST_EMAIL)

    assert len(results) == 1
    assert results[0].status == "no_citations_found"


def test_reference_number_not_in_list_is_its_own_status():
    results = process_sentence("s1", "A claim citing an unknown source [9].", {1: "10.1/a"}, TEST_EMAIL)

    assert len(results) == 1
    assert results[0].status == "reference_not_in_list"
    assert results[0].ref_number == 9


@patch("src.pipeline.process_pair")
def test_single_citation_gets_context_sole(mock_process_pair):
    mock_process_pair.return_value = None
    process_sentence("s1", "A claim with one source [1].", {1: "10.1/a"}, TEST_EMAIL)

    mock_process_pair.assert_called_once_with("s1", "A claim with one source [1].", 1, "10.1/a", "sole", TEST_EMAIL, None, None)


@patch("src.pipeline.process_pair")
def test_multiple_citations_get_context_one_of_several(mock_process_pair):
    mock_process_pair.return_value = None
    process_sentence("s1", "A claim with two sources [1][2].", {1: "10.1/a", 2: "10.1/b"}, TEST_EMAIL)

    assert mock_process_pair.call_count == 2
    for call in mock_process_pair.call_args_list:
        assert call.args[4] == "one_of_several"


@patch("src.pipeline.process_pair")
def test_range_expands_into_multiple_pairs(mock_process_pair):
    mock_process_pair.return_value = None
    process_sentence(
        "s1", "A claim with a range [1-3].",
        {1: "10.1/a", 2: "10.1/b", 3: "10.1/c"}, TEST_EMAIL,
    )

    assert mock_process_pair.call_count == 3


# ---------------------------------------------------------------------------
# process_sentences -- multi-sentence orchestration
# ---------------------------------------------------------------------------

@patch("src.pipeline.log_llm_call")
@patch("src.pipeline.get_verdict")
@patch("src.pipeline.fetch_source_content")
@patch("src.pipeline.convert_doi")
@patch("src.pipeline.resolve_doi")
def test_process_pair_emits_progress_events_when_tracker_passed(
    mock_resolve, mock_convert, mock_fetch, mock_verdict, mock_log
):
    mock_resolve.return_value = CrossrefResult(doi="10.1/x", status="resolved", title="A Paper")
    mock_convert.return_value = PmcIdResult(doi="10.1/x", status="converted", pmid="123", pmcid="PMC456")
    mock_fetch.return_value = ContentResult(status="full_text", text="The full body of the paper with enough words to pass the abstract-length guard and support quoting.")
    mock_verdict.return_value = VerdictResult(
        status="verdict", verdict="unsupported",
        source_type="full_text", citation_context="sole",
        assertions_checked="all", primary_family="directional_inversion",
        secondary_family="none", evidence_quote="The full body of the paper with enough words to pass the abstract-length guard and support quoting.",
        omitted_contradicting_evidence="", source_attribution="own_finding",
        explanation="Contradicts.", raw_response="{}",
    )

    tracker = ProgressTracker()
    result = process_pair("s1", "A claim [1].", 1, "10.1/x", "sole", TEST_EMAIL, progress=tracker)

    assert result.status == "verdict"
    events = tracker.snapshot()
    stages = [e["stage"] for e in events]
    assert "ids" in stages
    assert "content" in stages
    assert "llm" in stages
    assert "verdict" in stages
    assert any("No" in e["message"] for e in events if e["stage"] == "verdict")
    assert all(e["ref"] == 1 for e in events if e["ref"] is not None)


@patch("src.pipeline.process_sentence")
def test_process_sentences_calls_process_sentence_per_sentence(mock_process_sentence):
    mock_process_sentence.return_value = []
    sentences = [
        {"id": "s1", "text": "First sentence [1]."},
        {"id": "s2", "text": "Second sentence [2]."},
    ]
    process_sentences(sentences, {1: "10.1/a", 2: "10.1/b"}, TEST_EMAIL)

    assert mock_process_sentence.call_count == 2


@patch("src.analysis_runner.build_metadata_report")
@patch("src.analysis_runner.process_sentences")
def test_run_content_and_metadata_uses_metadata_gate_for_report_text(mock_process_sentences, mock_build_metadata_report):
    mock_process_sentences.return_value = [
        PairResult(
            sentence_id="s1",
            sentence_text="Sentence with unsupported claim [1].",
            ref_number=1,
            status="verdict",
            verdict="unsupported",
            source_type="full_text",
            citation_context="sole",
            evidence_quote="The paper says the opposite.",
            explanation="The source says the opposite.",
            llm_call_attempted=True,
            show_content=True,
        ),
        PairResult(
            sentence_id="s2",
            sentence_text="Sentence with supported claim [2].",
            ref_number=2,
            status="verdict",
            verdict="supported",
            source_type="full_text",
            citation_context="sole",
            evidence_quote="The paper says this.",
            explanation="The source supports it.",
            llm_call_attempted=True,
            show_content=True,
        ),
    ]
    mock_build_metadata_report.return_value = {
        "bibliography_status": [
            {"reference_id": 1, "status": "well_cited", "findings": [{"check_id": 9, "field": "title", "declared": "Old title", "actual": "New title"}]},
            {"reference_id": 2, "status": "well_cited", "findings": []},
        ],
        "document_level_findings": [],
        "not_evaluated_checks": [],
        "official_records": {
            "1": {"verified_title_match": True, "verified_first_author_match": True, "official_ref_text": "Old title. Example. 2019.", "actual_fields": {"title": "Old title", "authors": ["Example"], "year": 2019}},
            "2": {"verified_title_match": True, "verified_first_author_match": True, "official_ref_text": "Second title. Example. 2020.", "actual_fields": {"title": "Second title", "authors": ["Example"], "year": 2020}},
        },
    }

    pair_results, _ = run_content_and_metadata(
        [{"id": "s1", "text": "Sentence with unsupported claim [1]."}, {"id": "s2", "text": "Sentence with supported claim [2]."}],
        {1: {"number": 1, "doi": "10.1/a"}, 2: {"number": 2, "doi": "10.1/b"}},
        [{"number": 1, "doi": "10.1/a"}, {"number": 2, "doi": "10.1/b"}],
        "body text",
        TEST_EMAIL,
    )

    assert len(pair_results) == 2
    assert pair_results[0].show_content is True
    assert pair_results[0].report_text_result is not None
    assert pair_results[1].show_content is False
    assert pair_results[1].report_text_result is None


# ---------------------------------------------------------------------------
# SourceCache -- the same DOI cited by multiple sentences should only hit
# Crossref / NCBI / the content fetch once per pipeline run.
# ---------------------------------------------------------------------------

@patch("src.pipeline.fetch_source_content")
@patch("src.pipeline.convert_doi")
@patch("src.pipeline.resolve_doi")
def test_same_doi_cited_twice_only_fetches_once(mock_resolve, mock_convert, mock_fetch):
    mock_resolve.return_value = CrossrefResult(doi="10.1/shared", status="resolved", title="A Paper")
    mock_convert.return_value = PmcIdResult(doi="10.1/shared", status="converted", pmid="123", pmcid="PMC1")
    mock_fetch.return_value = ContentResult(status="full_text", source="pmc", text="Full body text.")

    reference_list = {6: "10.1/shared"}
    sentences = [
        {"id": "s1", "text": "First claim citing the shared source [6]."},
        {"id": "s2", "text": "Second claim citing the same shared source [6]."},
    ]

    with patch("src.pipeline.get_verdict") as mock_verdict, patch("src.pipeline.log_llm_call"):
        mock_verdict.return_value = VerdictResult(
            status="verdict", source_type="full_text", citation_context="sole",
            assertions_checked="all", verdict="supported", primary_family="none",
            secondary_family="none", evidence_quote="Full body text.",
            omitted_contradicting_evidence="", source_attribution="own_finding",
            explanation="matches",
        )
        results = process_sentences(sentences, reference_list, TEST_EMAIL, max_workers=2)

    assert len(results) == 2
    assert all(r.status == "verdict" for r in results)
    # The whole point of the cache: two sentences, same DOI, one network chain.
    mock_resolve.assert_called_once()
    mock_convert.assert_called_once()
    mock_fetch.assert_called_once()


@patch("src.pipeline.resolve_doi")
def test_different_dois_each_get_their_own_fetch(mock_resolve):
    mock_resolve.side_effect = lambda doi, contact_email: CrossrefResult(doi=doi, status="not_found")

    reference_list = {1: "10.1/a", 2: "10.1/b"}
    sentences = [
        {"id": "s1", "text": "Claim citing one source [1]."},
        {"id": "s2", "text": "Claim citing a different source [2]."},
    ]
    results = process_sentences(sentences, reference_list, TEST_EMAIL, max_workers=2)

    assert len(results) == 2
    assert all(r.status == "doi_not_found" for r in results)
    assert mock_resolve.call_count == 2


# ---------------------------------------------------------------------------
# Identifier-free references (no DOI): the extended cascade must be reachable
# through the pipeline (Eduardo's audit issue 1). No Crossref / NCBI step,
# no drop at parse time, no synthetic "not indexed" verdict.
# ---------------------------------------------------------------------------

def test_reference_without_doi_and_without_search_keys_is_unresolvable():
    results = process_sentence(
        "s1", "A claim citing an unidentifiable reference [1].",
        {1: {"number": 1}}, TEST_EMAIL,
    )

    assert len(results) == 1
    assert results[0].status == "unresolvable_reference"
    assert "no DOI" in results[0].error_message


@patch("src.pipeline.process_pair")
def test_identifier_free_reference_is_passed_through_to_process_pair(mock_process_pair):
    mock_process_pair.return_value = None
    entries = {1: {"number": 1, "title": "A Paper", "first_author": "Smith", "publication_year": 2020}}

    process_sentence("s1", "A claim citing a DOI-less source [1].", entries, TEST_EMAIL)

    mock_process_pair.assert_called_once_with(
        "s1", "A claim citing a DOI-less source [1].", 1, entries[1], "sole", TEST_EMAIL, None, None,
    )


@patch("src.pipeline.log_llm_call")
@patch("src.pipeline.get_verdict")
@patch("src.pipeline.convert_doi")
@patch("src.pipeline.resolve_doi")
@patch("src.pipeline.fetch_source_content")
def test_no_doi_reference_searches_by_title_first_author_year(
    mock_fetch, mock_resolve, mock_convert, mock_llm, mock_log
):
    mock_fetch.return_value = ContentResult(
        status="full_text", source="openalex",
        text="The body of a DOI-less paper with enough words to pass the "
             "abstract-length guard cleanly and support a verdict.",
    )
    mock_llm.return_value = VerdictResult(
        status="verdict", verdict="supported", assertions_checked="the claim",
        primary_family=None, secondary_family="none", evidence_quote="body",
        omitted_contradicting_evidence="", source_attribution="own_finding",
        explanation="Supported.", quote_guard_triggered=False,
    )

    reference_list = {1: {"number": 1, "title": "A DOI-less Paper", "first_author": "Smith", "publication_year": 2020}}
    results = process_sentence("s1", "A claim citing a DOI-less source [1].", reference_list, TEST_EMAIL)

    assert len(results) == 1
    assert results[0].status == "verdict"
    assert results[0].doi is None
    # Identifier-free: the search keys go straight to the extended cascade and
    # the classic Crossref -> NCBI chain is never touched.
    mock_resolve.assert_not_called()
    mock_convert.assert_not_called()
    mock_fetch.assert_called_once_with(
        doi=None, title="A DOI-less Paper", first_author="Smith", publication_year=2020,
        contact_email=TEST_EMAIL,
    )


@patch("src.pipeline.resolve_doi")
@patch("src.pipeline.fetch_source_content")
def test_no_doi_reference_unavailable_is_source_could_not_be_opened(mock_fetch, mock_resolve):
    mock_fetch.return_value = ContentResult(status="unavailable")

    reference_list = {1: {"number": 1, "title": "A DOI-less Paper", "first_author": "Smith", "publication_year": 2020}}
    results = process_sentence("s1", "A claim citing a DOI-less source [1].", reference_list, TEST_EMAIL)

    assert len(results) == 1
    assert results[0].status == "source_could_not_be_opened"
    mock_resolve.assert_not_called()


@patch("src.pipeline.resolve_doi")
@patch("src.pipeline.fetch_source_content")
def test_no_doi_reference_ambiguous_source_abstains(mock_fetch, mock_resolve):
    mock_fetch.return_value = ContentResult(status="abstained", error_message="ambiguous open-access match")

    reference_list = {1: {"number": 1, "title": "A DOI-less Paper", "first_author": "Smith", "publication_year": 2020}}
    results = process_sentence("s1", "A claim citing a DOI-less source [1].", reference_list, TEST_EMAIL)

    assert len(results) == 1
    assert results[0].status == "source_abstained"
    mock_resolve.assert_not_called()


@patch("src.pipeline.log_llm_call")
@patch("src.pipeline.get_verdict")
@patch("src.pipeline.resolve_doi")
@patch("src.pipeline.fetch_source_content")
def test_no_doi_reference_with_raw_bibliography_parses_search_keys(
    mock_fetch, mock_resolve, mock_llm, mock_log
):
    mock_fetch.return_value = ContentResult(
        status="abstract_only", source="semantic_scholar",
        text="A genuinely usable abstract with enough length to pass the "
             "guard check and support a real verdict.",
    )
    mock_llm.return_value = VerdictResult(
        status="verdict", verdict="supported", assertions_checked="the claim",
        primary_family=None, secondary_family="none", evidence_quote="the abstract",
        omitted_contradicting_evidence="", source_attribution="own_finding",
        explanation="Supported.", quote_guard_triggered=False,
    )

    reference_list = {
        1: {
            "number": 1,
            "raw": "[1] Faught, E., & Vijayan, M. M. (2018). The mineralocorticoid receptor "
                  "is essential. Communications Biology, 1, 195.",
        }
    }
    results = process_sentence("s1", "A claim citing a DOI-less APA reference [1].", reference_list, TEST_EMAIL)

    assert len(results) == 1
    assert results[0].status == "verdict"
    assert results[0].source_type == "abstract"
    mock_resolve.assert_not_called()
    call_kwargs = mock_fetch.call_args.kwargs
    assert call_kwargs["doi"] is None
    assert call_kwargs["title"] == "The mineralocorticoid receptor is essential"
    assert call_kwargs["first_author"] == "Faught"
    assert call_kwargs["publication_year"] == 2018


@patch("src.pipeline.log_llm_call")
@patch("src.pipeline.get_verdict")
@patch("src.pipeline.fetch_source_content")
def test_same_no_doi_reference_cited_twice_fetches_once(mock_fetch, mock_verdict, mock_log):
    mock_fetch.return_value = ContentResult(status="full_text", source="openalex", text="Full body text.")
    mock_verdict.return_value = VerdictResult(
        status="verdict", verdict="supported", assertions_checked="all",
        primary_family="none", secondary_family="none", evidence_quote="Full body text.",
        omitted_contradicting_evidence="", source_attribution="own_finding",
        explanation="matches", quote_guard_triggered=False,
    )

    reference_list = {6: {"number": 6, "title": "A DOI-less Paper", "first_author": "Smith", "publication_year": 2020}}
    sentences = [
        {"id": "s1", "text": "First claim citing the DOI-less source [6]."},
        {"id": "s2", "text": "Second claim citing the same DOI-less source [6]."},
    ]

    results = process_sentences(sentences, reference_list, TEST_EMAIL, max_workers=2)

    assert len(results) == 2
    assert all(r.status == "verdict" for r in results)
    # Same title/author/year triple -> one shared extended chain, one fetch.
    mock_fetch.assert_called_once()
