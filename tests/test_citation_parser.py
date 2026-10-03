from src.citation_parser import parse_citation_markers
from src.pipeline import resolve_reference_numbers_for_sentence


def test_single_bracket():
    assert parse_citation_markers("Cortisol impairs muscle growth [3].") == [3]


def test_multiple_separate_brackets():
    # "[3][8]" per the integration note
    assert parse_citation_markers("A sentence citing two sources [3][8].") == [3, 8]


def test_comma_separated_within_one_bracket():
    # "[4,5]" per the integration note
    assert parse_citation_markers("A claim with a comma list [4,5].") == [4, 5]


def test_hyphen_range_is_expanded():
    # "[3-5]" must expand to three references, not one
    assert parse_citation_markers("A claim with a range [3-5].") == [3, 4, 5]


def test_en_dash_range_is_expanded():
    # "[3\u20135]" -- PDFs commonly use en dash instead of hyphen
    assert parse_citation_markers("A claim with a range [3\u20135].") == [3, 4, 5]


def test_mixed_comma_and_range_in_one_bracket():
    assert parse_citation_markers("Mixed group [3,5-7].") == [3, 5, 6, 7]


def test_no_brackets_returns_empty_list():
    assert parse_citation_markers("A sentence with no citations at all.") == []


def test_duplicate_reference_numbers_deduplicated():
    assert parse_citation_markers("Cites the same source twice [3][3].") == [3]


def test_reversed_range_still_expands_correctly():
    assert parse_citation_markers("A backwards range [5-3].") == [3, 4, 5]


def test_realistic_sentence_with_three_separate_groups():
    text = "Chronic stress reduces growth [1], alters composition [2,3], and suppresses appetite [4-6]."
    assert parse_citation_markers(text) == [1, 2, 3, 4, 5, 6]


def test_author_year_citations_resolve_to_reference_numbers():
    text = "Smith et al. (2020) reported this effect."
    reference_list = {
        1: {"number": 1, "raw": "Smith A. Example Paper. 2020."},
        2: {"number": 2, "raw": "Jones B. Other Paper. 2021."},
    }
    assert resolve_reference_numbers_for_sentence(text, reference_list) == [1]
