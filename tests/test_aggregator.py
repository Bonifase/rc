from src.pipeline import PairResult
from src.aggregator import build_sentence_records, build_document_summary, generate_report


def _verdict_pair(sentence_id, sentence_text, ref_number, verdict):
    return PairResult(
        sentence_id=sentence_id, sentence_text=sentence_text, ref_number=ref_number,
        doi=f"10.1/{ref_number}", status="verdict", verdict=verdict,
        source_type="full_text", citation_context="sole", llm_call_attempted=True,
    )


def test_single_sentence_single_pair_verdict_passes_through():
    pairs = [_verdict_pair("s1", "A claim.", 1, "supported")]

    records = build_sentence_records(pairs)

    assert len(records) == 1
    assert records[0].verdict == "supported"
    assert records[0].aggregation_level == 4
    assert records[0].has_incomplete_pairs is False


def test_contradiction_beats_everything_supports():
    pairs = [
        _verdict_pair("s1", "A claim citing two sources.", 1, "supported"),
        _verdict_pair("s1", "A claim citing two sources.", 2, "unsupported"),
    ]

    records = build_sentence_records(pairs)

    assert len(records) == 1
    assert records[0].verdict == "unsupported"
    assert records[0].aggregation_level == 1


def test_some_uncertain_beats_everything_supports_but_not_contradiction():
    pairs = [
        _verdict_pair("s1", "claim", 1, "supported"),
        _verdict_pair("s1", "claim", 2, "uncertain"),
    ]
    record = build_sentence_records(pairs)[0]
    assert record.verdict == "uncertain"
    assert record.aggregation_level == 3

    pairs2 = [
        _verdict_pair("s1", "claim", 1, "uncertain"),
        _verdict_pair("s1", "claim", 2, "unsupported"),
    ]
    record2 = build_sentence_records(pairs2)[0]
    assert record2.verdict == "unsupported"
    assert record2.aggregation_level == 1


def test_all_pairs_incomplete_is_no_comparable_evidence():
    pairs = [
        PairResult(sentence_id="s1", sentence_text="claim", ref_number=1, doi="10.1/a", status="doi_not_found"),
        PairResult(sentence_id="s1", sentence_text="claim", ref_number=2, doi="10.1/b", status="source_not_indexed"),
    ]

    records = build_sentence_records(pairs)

    assert records[0].verdict is None
    assert records[0].aggregation_level == 2
    assert records[0].has_incomplete_pairs is True


def test_supported_plus_incomplete_pair_is_some_uncertain_not_everything_supports():
    # The four-level aggregation is deliberately NOT a worst-of-the-verdicts:
    # a supported source next to a source that could not be compared is
    # level 3 ("some uncertain / not comparable"), not level 4 ("everything
    # supports"). The failure is not folded into a content verdict (Guard 2),
    # but it is also not ignored.
    pairs = [
        _verdict_pair("s1", "claim", 1, "supported"),
        PairResult(sentence_id="s1", sentence_text="claim", ref_number=2, doi="10.1/b", status="technical_failure"),
    ]

    records = build_sentence_records(pairs)

    assert records[0].verdict == "uncertain"
    assert records[0].aggregation_level == 3
    assert records[0].has_incomplete_pairs is True


def test_sentence_grouping_preserves_first_appearance_order():
    pairs = [
        _verdict_pair("s2", "second sentence", 1, "supported"),
        _verdict_pair("s1", "first sentence", 1, "supported"),
        _verdict_pair("s2", "second sentence", 2, "supported"),
    ]

    records = build_sentence_records(pairs)

    assert [r.sentence_id for r in records] == ["s2", "s1"]
    assert len(records[0].pairs) == 2  # s2 has two pairs grouped together


def test_technical_failure_pair_does_not_count_as_a_verdict_but_flags_incomplete():
    pairs = [
        PairResult(sentence_id="s1", sentence_text="claim", ref_number=1, doi="10.1/a", status="technical_failure"),
    ]

    records = build_sentence_records(pairs)

    assert records[0].verdict is None
    assert records[0].aggregation_level == 2
    assert records[0].has_incomplete_pairs is True


def test_mixed_verdict_and_technical_failure_still_reports_the_real_verdict():
    # One source gave a real verdict, another failed technically -- the
    # sentence should still show the real verdict, not swallow it because
    # of the unrelated failure (Guard 2's spirit applied at aggregation time).
    # A contradiction wins outright, so this is still level 1.
    pairs = [
        _verdict_pair("s1", "claim", 1, "unsupported"),
        PairResult(sentence_id="s1", sentence_text="claim", ref_number=2, doi="10.1/b", status="technical_failure"),
    ]

    records = build_sentence_records(pairs)

    assert records[0].verdict == "unsupported"
    assert records[0].aggregation_level == 1
    assert records[0].has_incomplete_pairs is True


def test_no_citations_found_sentence_has_null_verdict():
    pairs = [PairResult(sentence_id="s1", sentence_text="No citations here.", status="no_citations_found")]

    records = build_sentence_records(pairs)

    assert records[0].verdict is None
    assert records[0].aggregation_level == 2
    assert records[0].has_incomplete_pairs is True


def test_summary_counts_sentences_and_checks_separately():
    pairs = [
        _verdict_pair("s1", "sentence with two sources", 1, "supported"),
        _verdict_pair("s1", "sentence with two sources", 2, "supported"),
        _verdict_pair("s2", "sentence with one source", 1, "unsupported"),
    ]
    records = build_sentence_records(pairs)

    summary = build_document_summary(pairs, records)

    assert summary["total_sentences"] == 2
    assert summary["total_checks"] == 3  # 3 pairs, not 2 sentences


def test_no_citations_found_pair_does_not_count_as_a_check():
    pairs = [
        _verdict_pair("s1", "checked sentence", 1, "supported"),
        PairResult(sentence_id="s2", sentence_text="unchecked sentence", status="no_citations_found"),
    ]
    records = build_sentence_records(pairs)

    summary = build_document_summary(pairs, records)

    assert summary["total_sentences"] == 2
    assert summary["total_checks"] == 1  # the no-citations pair isn't a check


def test_pairs_that_never_reached_the_llm_do_not_count_as_checks():
    # Per the integration note, "checks" means model calls specifically --
    # a pair that failed before the LLM was ever invoked (DOI didn't
    # resolve here) is a real pair, but not a check.
    pairs = [
        _verdict_pair("s1", "checked", 1, "supported"),
        PairResult(sentence_id="s2", sentence_text="x", ref_number=1, doi="10.1/x", status="doi_not_found"),
        PairResult(sentence_id="s3", sentence_text="y", ref_number=1, doi="10.1/y", status="source_not_indexed"),
    ]
    records = build_sentence_records(pairs)

    summary = build_document_summary(pairs, records)

    assert summary["total_checks"] == 1  # only the one pair that actually reached the LLM


def test_technical_failure_at_llm_stage_still_counts_as_a_check():
    # A technical failure AT the LLM step is still an attempted model call
    # (it just failed) -- distinct from a pair that never got that far.
    pairs = [
        PairResult(
            sentence_id="s1", sentence_text="x", ref_number=1, doi="10.1/x",
            status="technical_failure", llm_call_attempted=True,
        ),
    ]
    records = build_sentence_records(pairs)

    summary = build_document_summary(pairs, records)

    assert summary["total_checks"] == 1


def test_sentence_verdict_counts_use_no_comparable_evidence_bucket():
    pairs = [
        _verdict_pair("s1", "checked", 1, "supported"),
        PairResult(sentence_id="s2", sentence_text="failed", ref_number=1, doi="10.1/x", status="technical_failure"),
    ]
    records = build_sentence_records(pairs)

    summary = build_document_summary(pairs, records)

    assert summary["sentence_verdict_counts"] == {"supported": 1, "no comparable evidence": 1}


def test_pair_status_counts_track_non_verdict_outcomes_separately_from_verdicts():
    pairs = [
        _verdict_pair("s1", "checked", 1, "supported"),
        PairResult(sentence_id="s2", sentence_text="x", ref_number=1, doi="10.1/x", status="doi_not_found"),
        PairResult(sentence_id="s3", sentence_text="y", ref_number=1, doi="10.1/y", status="technical_failure"),
    ]
    records = build_sentence_records(pairs)

    summary = build_document_summary(pairs, records)

    assert summary["pair_status_counts"] == {"verdict": 1, "doi_not_found": 1, "technical_failure": 1}


def test_generate_report_produces_full_shape():
    pairs = [_verdict_pair("s1", "A claim.", 1, "supported")]

    report = generate_report(pairs)

    assert len(report["sentences"]) == 1
    assert report["sentences"][0]["sentence_text"] == "A claim."
    assert report["sentences"][0]["verdict"] == "supported"
    assert report["sentences"][0]["aggregation_level"] == 4
    assert len(report["sentences"][0]["pairs"]) == 1
    assert report["summary"]["total_sentences"] == 1
    assert report["summary"]["total_checks"] == 1
