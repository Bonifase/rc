from src.metadata.extraction import parse_reference_entry, expand_citation_markers, Author


def test_full_apa_entry_parses_every_field():
    raw = (
        "Faught, E., & Vijayan, M. M. (2018). The mineralocorticoid receptor "
        "is essential for stress axis regulation in zebrafish larvae. "
        "Communications Biology, 1, 195. "
        "https://doi.org/10.1038/s41598-018-36681-w"
    )
    r = parse_reference_entry(11, raw)

    assert r.reference_id == 11
    assert r.raw == raw
    assert [(a.family, a.given) for a in r.authors] == [("Faught", "E."), ("Vijayan", "M. M.")]
    assert r.year == 2018
    assert r.title == "The mineralocorticoid receptor is essential for stress axis regulation in zebrafish larvae"
    assert r.journal == "Communications Biology"
    assert r.volume == "1"
    assert r.issue is None
    assert r.pages == "195"
    assert r.doi == "10.1038/s41598-018-36681-w"
    assert r.issn is None  # never present in raw text
    assert r.authors_truncated is False


def test_entry_with_issue_and_page_range_and_truncated_authors():
    raw = (
        "Lien, S., Koop, B. F., Sandve, S. R., ... Davidson, W. S. (2016). "
        "The Atlantic salmon genome provides insights into rediploidization. "
        "Nature, 533(7602), 200-205. https://doi.org/10.1038/nature17164"
    )
    r = parse_reference_entry(1, raw)

    assert r.year == 2016
    assert r.journal == "Nature"
    assert r.volume == "533"
    assert r.issue == "7602"
    assert r.pages == "200-205"
    assert r.doi == "10.1038/nature17164"
    assert r.authors_truncated is True
    assert r.authors[0] == Author(family="Lien", given="S.")
    assert r.authors[-1] == Author(family="Davidson", given="W. S.")


def test_entry_without_doi_still_parses_other_fields():
    raw = "Smith, J. (2020). A title with no DOI. Some Journal, 5, 10-20."
    r = parse_reference_entry(2, raw)

    assert r.doi is None
    assert r.year == 2020
    assert r.journal == "Some Journal"
    assert r.volume == "5"
    assert r.pages == "10-20"


def test_entry_with_no_parseable_year_leaves_downstream_fields_none_not_empty():
    raw = "This is not a citation at all, just some stray text."
    r = parse_reference_entry(3, raw)

    assert r.year is None
    assert r.authors == []
    assert r.title is None
    assert r.journal is None
    # Explicitly None, never "" -- per the spec's null-vs-empty-string rule
    assert r.title is not ""
    assert r.journal is not ""


def test_raw_is_always_preserved_even_when_nothing_else_parses():
    raw = "??? garbled entry ??? 10.1038/abc123"
    r = parse_reference_entry(4, raw)
    assert r.raw == raw
    assert r.doi == "10.1038/abc123"


def test_et_al_marker_also_flags_truncation():
    raw = "Doe, J., et al. (2019). Some title. Journal Name, 2, 5-9."
    r = parse_reference_entry(5, raw)
    assert r.authors_truncated is True


def test_expand_citation_markers_handles_ranges_and_multiple_groups():
    assert expand_citation_markers("A claim [3-5].") == [3, 4, 5]
    assert expand_citation_markers("A claim [3][8].") == [3, 8]
    assert expand_citation_markers("A claim [3,8].") == [3, 8]
    assert expand_citation_markers("A claim [3, 8].") == [3, 8]
    assert expand_citation_markers("A claim [3\u20135].") == [3, 4, 5]
    assert expand_citation_markers("No citations here.") == []


def test_build_declared_references_parses_raw_when_present():
    from src.metadata.extraction import build_declared_references
    entries = [{"number": 1, "doi": "10.1038/nature17164", "raw": (
        "Lien, S., Koop, B. F. (2016). The Atlantic salmon genome. "
        "Nature, 533(7602), 200-205. https://doi.org/10.1038/nature17164"
    )}]
    refs = build_declared_references(entries)
    assert len(refs) == 1
    assert refs[0].journal == "Nature"
    assert refs[0].volume == "533"


def test_build_declared_references_degrades_gracefully_without_raw():
    from src.metadata.extraction import build_declared_references
    entries = [{"number": 1, "doi": "10.1038/nature17164"}]
    refs = build_declared_references(entries)
    assert len(refs) == 1
    assert refs[0].doi == "10.1038/nature17164"
    assert refs[0].journal is None
    assert refs[0].authors == []
