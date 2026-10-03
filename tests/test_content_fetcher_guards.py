from src.content_fetcher import is_usable_abstract


def test_usable_abstract_passes():
    text = "Cortisol administration markedly increased ubiquitin-proteasome transcripts and total proteolytic activity in white muscle."
    assert is_usable_abstract(text) is True


def test_empty_string_is_not_usable():
    assert is_usable_abstract("") is False


def test_none_is_not_usable():
    assert is_usable_abstract(None) is False


def test_too_short_is_not_usable():
    assert is_usable_abstract("Too short.") is False


def test_placeholder_text_is_not_usable():
    assert is_usable_abstract("No abstract available") is False
    assert is_usable_abstract("N/A") is False
    assert is_usable_abstract("not available") is False


def test_placeholder_check_is_case_insensitive():
    assert is_usable_abstract("NO ABSTRACT AVAILABLE") is False


def test_whitespace_only_is_not_usable():
    assert is_usable_abstract("     ") is False


def test_custom_min_length_respected():
    text = "This is exactly forty two characters long!!"
    assert len(text) == 43
    assert is_usable_abstract(text, min_length=50) is False
    assert is_usable_abstract(text, min_length=10) is True
