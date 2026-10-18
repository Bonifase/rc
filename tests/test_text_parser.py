from src.text_parser import _split_into_sentences, parse_pasted_text


def test_numbered_heading_is_a_boundary_not_a_sentence_tail():
    # Regression for the heading-boundary bug: the heading number "2." used
    # to be glued onto the tail of the previous sentence because the splitter
    # only breaks before uppercase letters and brackets, not digits.
    body = (
        "Cortisol operates through the glucocorticoid receptor to set the "
        "stress axis [1].\n\n"
        "2. Ligand specificity of the teleost mineralocorticoid receptor\n\n"
        "The pharmacological case for DOC rests on transactivation assays "
        "in rainbow trout cells [2]."
    )

    sentences = _split_into_sentences(body)

    assert len(sentences) == 2
    assert sentences[0].endswith("stress axis [1].")
    assert not sentences[0].endswith("2.")
    assert sentences[1].startswith("The pharmacological case")
    assert sentences[1].endswith("rainbow trout cells [2].")


def test_multiple_numbered_headings_never_bleed_into_sentences():
    body = (
        "Opening text that is a single claim [1].\n\n"
        "1. Introduction\n\n"
        "Intro claim here [2].\n\n"
        "2. Methods\n\n"
        "Method claim here [3].\n\n"
        "3. Results\n\n"
        "Result claim here [4]."
    )

    sentences = _split_into_sentences(body)

    assert len(sentences) == 4
    for s, marker in zip(sentences, ("[1]", "[2]", "[3]", "[4]")):
        assert s.endswith(marker + ".")


def test_paragraph_breaks_without_headings_still_join_into_sentences():
    # Ordinary blank-line paragraph breaks are NOT boundaries -- a sentence
    # can legitimately span two paragraphs.
    body = (
        "The first paragraph introduces the axis and its ligands [1].\n\n"
        "The second paragraph continues the claim about DOC [2]."
    )

    sentences = _split_into_sentences(body)

    assert len(sentences) == 2
    assert sentences[0].endswith("ligands [1].")
    assert sentences[1].endswith("DOC [2].")


def test_parse_pasted_text_with_numbered_headings_keeps_sentences_clean():
    text = (
        "Introduction\n\n"
        "Cortisol drives the stress response through the glucocorticoid "
        "receptor [1].\n\n"
        "2. Ligand specificity\n\n"
        "DOC binds the mineralocorticoid receptor in trout [2].\n\n"
        "References\n"
        "[1] Example A. 2024. https://doi.org/10.1000/one\n"
        "[2] Example B. 2024. https://doi.org/10.1000/two"
    )

    result = parse_pasted_text(text)

    assert len(result.sentences) == 2
    assert result.sentences[0]["text"].endswith("receptor [1].")
    assert result.sentences[1]["text"].endswith("trout [2].")
    assert result.sentences[1]["text"].startswith("DOC binds")
    assert len(result.reference_list) == 2


def test_reference_list_entries_include_raw_text_for_the_metadata_module():
    text = (
        "A claim citing a source [1].\n\n"
        "References\n"
        "[1] Faught, E., & Vijayan, M. M. (2018). The mineralocorticoid receptor "
        "is essential. Communications Biology, 1, 195. https://doi.org/10.1038/s41598-018-36681-w"
    )
    result = parse_pasted_text(text)
    assert len(result.reference_list) == 1
    entry = result.reference_list[0]
    assert entry["number"] == 1
    assert entry["doi"] == "10.1038/s41598-018-36681-w"
    assert entry["raw"].startswith("[1] Faught, E., & Vijayan, M. M. (2018)")


def test_identifier_free_reference_is_kept_with_raw_text():
    text = (
        "A claim citing a source [1].\n\n"
        "References\n"
        "[1] Smith, J. (2020). A paper without a DOI. Journal of Everything, 2, 10-20."
    )
    result = parse_pasted_text(text)

    assert len(result.reference_list) == 1
    entry = result.reference_list[0]
    assert entry["number"] == 1
    assert "doi" not in entry
    assert entry["raw"].startswith("[1] Smith, J. (2020)")
    assert any("no DOI" in w for w in result.warnings)


def test_author_year_citations_are_kept_when_reference_header_exists():
    text = (
        "Smith et al. (2020) showed this effect in teleost fish.\n\n"
        "References\n"
        "Smith, A. Example Paper. 2020. https://doi.org/10.1000/example"
    )

    result = parse_pasted_text(text)

    assert len(result.sentences) == 1
    assert "Smith et al. (2020)" in result.sentences[0]["text"]


def test_full_bibliography_entry_without_numbering_is_preserved():
    text = (
        "Muscle growth in teleosts is highly regenerative (Manneken et al., 2022).\n\n"
        "References\n"
        "Manneken, J. D., Dauer, M. V. P., & Currie, P. D. (2022). Dynamics of muscle growth and regeneration: Lessons from the teleost. Experimental Cell Research, 411(2), 112991. https://doi.org/10.1016/j.yexcr.2021.112991"
    )

    result = parse_pasted_text(text)

    assert len(result.reference_list) == 1
    entry = result.reference_list[0]
    assert entry["raw"].startswith("Manneken, J. D.")
    assert "Experimental Cell Research" in entry["raw"]
    assert entry["doi"] == "10.1016/j.yexcr.2021.112991"


def test_wrapped_reference_lines_stay_in_one_entry():
    text = (
        "Muscle growth is plastic (Ali et al., 2022).\n\n"
        "References\n"
        "Ali, A., Shaalan, W. M., Al-Tobasei, R., & Salem, M. (2022). Coding and noncoding genes in\n"
        "Nile tilapia. Cells, 11(16), 2504. https://doi.org/10.3390/cells11162504\n"
        "Canosa, L. F., & Bertucci, J. I. (2023). The effect of stressors. Frontiers in Endocrinology, 14,\n"
        "1109461. https://doi.org/10.3389/fendo.2023.1109461\n"
        "Duran, B. O. da S., Zanella, B. T. T., Perez, E. S., Mareco, E. A., Blasco, J., Dal-\n"
        "Pai-Silva, M., & Garcia de la Serrana, D. (2022). Amino acids and IGF1. https://doi.org/10.3390/ijms23031180\n"
    )

    result = parse_pasted_text(text)

    assert [entry["number"] for entry in result.reference_list] == [1, 2, 3]
    assert result.reference_list[0]["doi"] == "10.3390/cells11162504"
    assert result.reference_list[1]["raw"].endswith("fendo.2023.1109461")
    assert result.reference_list[2]["raw"].startswith("Duran, B. O. da S.")


def test_parenthetical_author_year_citations_are_detected_as_cited_sentences():
    text = (
        "Fibers grow by hypertrophy and hyperplasia (Johnston et al., 2011). "
        "This sentence cites nothing.\n\n"
        "References\n"
        "Johnston, I. A., Bower, N. I., & Macqueen, D. J. (2011). Growth. https://doi.org/10.1242/jeb.038620"
    )

    result = parse_pasted_text(text)

    assert len(result.sentences) == 1
    assert "Johnston et al., 2011" in result.sentences[0]["text"]
