"""Name-first author lists, split DOIs and Crossref title markup (2026-10-21).

Fixture: the ten bibliography entries of ELICIT_IGF1_Pathway_Regulatory_Mechanisms.docx,
as src.text_parser reads them. Before this change the metadata module produced four
false or misdescribed cards on this document and hid three references from the report
(identity unconfirmed). Zero network: Crossref records are built in the test.
"""

import sys
from pathlib import Path

import pytest

from src.doi_utils import extract_doi, find_doi
from src.metadata.checks.phase_a import check_17_doi_whitespace
from src.metadata.crossref_metadata import clean_crossref_text, title_similarity
from src.metadata.document import MetadataDoc
from src.metadata.extraction import build_declared_references, parse_reference_entry
from src.text_parser import _extract_doi_from_line

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reference"))
from metadata_status_mapping import classify_reference  # noqa: E402
from render_metadata_cards import _readable_field  # noqa: E402


ELICIT_REFERENCES = [
    {
        "number": 1,
        "raw": "1. M. Codina, D. García de la Serrana, J. Sanchez-Gurmaches, et al (2008) Metabolic and mitogenic effects of IGF-II in rainbow trout (Oncorhynchus mykiss) myocytes in culture and the role of IGF-II in the PI3K/Akt and MAPK signalling pathways. General and Comparative Endocrinology. https://doi.org/10.1016/j.ygcen. 2008.04.009"
    },
    {
        "number": 2,
        "raw": "2. B. Cleveland, G. M. Weber (2010) Effects of insulin-like growth factor-I, insulin, and leucine on protein turnover and ubiquitin ligase expression in rainbow trout primary myocytes. American Journal of Physiology Regulatory Integrative and Comparative Physiology. https://doi.org/10.1152/ajpregu.00516.2009"
    },
    {
        "number": 3,
        "raw": "3. Emilio J. Vélez, Esmail Lutfi, Vanesa Jiménez-Amilburu, et al (2014) IGF-I and amino acids effects through TOR signaling on proliferation and differentiation of gilthead sea bream cultured myocytes. General and Comparative Endocrinology. https://doi.org/10.1016/j.ygcen.2014.05.024"
    },
    {
        "number": 4,
        "raw": "4. B. O. S. Duran, B. T. T. Zanella, É. S. Perez, et al (2022) Amino Acids and IGF1 Regulation of Fish Muscle Growth Revealed by Transcriptome and microRNAome Integrative Analyses of Pacu (Piaractus mesopotamicus) Myotubes. International Journal of Molecular Sciences. https://doi.org/10.3390/ijms23031180"
    },
    {
        "number": 5,
        "raw": "5. Yuliang Wei, Mengqing Liang, Houguo Xu (2020) Fish protein hydrolysate affected amino acid absorption and related gene expressions of IGF‐1/AKT pathways in turbot ( Scophthalmus maximus ). Aquaculture Nutrition. https://doi.org/10.1111/anu.12976"
    },
    {
        "number": 6,
        "raw": "6. E. Fuentes, I. Einarsdóttir, R. Paredes, et al (2015) The TORC1/P70S6K and TORC1/4EBP1 signaling pathways have a stronger contribution on skeletal muscle growth than MAPK/ERK in an early vertebrate: Differential involvement of the IGF system and atrogenes. General and Comparative Endocrinology. https://doi.org/10.1016/j.ygcen.2014.10.012 11"
    },
    {
        "number": 7,
        "raw": "7. M. Codina, N. Montserrat, I. Navarro, J. Gutiérrez (2006) Role of insulin and IGFs in fish muscle development and quality"
    },
    {
        "number": 8,
        "raw": "8. E. Fuentes, B. Björnsson, J. Valdés, et al (2011) IGF-I/PI3K/Akt and IGF-I/MAPK/ERK pathways in vivo in skeletal muscle are regulated by nutrition and contribute to somatic growth in the fine flounder. American Journal of Physiology Regulatory Integrative and Comparative Physiology. https://doi.org/10.1152/ajpregu. 00535.2010"
    },
    {
        "number": 9,
        "raw": "9. B. Nornberg, M. Figueiredo, L. F. Marins (2016) Expression profile of IGF paralog genes in liver and muscle of a GH-transgenic zebrafish. General and Comparative Endocrinology. https://doi.org/10.1016/j.ygcen.2015.12.017"
    },
    {
        "number": 10,
        "raw": "10. Xin Zhu, Linrui Ren, Jingjie Liu, et al (2021) Transcriptome analysis provides novel insights into the function of PI3K/AKT pathway in maintaining metabolic homeostasis of Chinese perch muscle. Aquaculture Reports. https://doi.org/10.1016/j.aqrep.2021.100838"
    }
]

# (number, [(family, given), ...] first three authors as parsed, year, doi, doi_as_written)
EXPECTED = {
    1: ([("Codina", "M."), ("García de la Serrana", "D."), ("Sanchez-Gurmaches", "J.")], 2008,
        "10.1016/j.ygcen.2008.04.009", "10.1016/j.ygcen. 2008.04.009"),
    2: ([("Cleveland", "B."), ("Weber", "G. M.")], 2010, "10.1152/ajpregu.00516.2009", None),
    3: ([("Vélez", "Emilio J."), ("Lutfi", "Esmail"), ("Jiménez-Amilburu", "Vanesa")], 2014,
        "10.1016/j.ygcen.2014.05.024", None),
    4: ([("Duran", "B. O. S."), ("Zanella", "B. T. T."), ("Perez", "É. S.")], 2022, "10.3390/ijms23031180", None),
    5: ([("Wei", "Yuliang"), ("Liang", "Mengqing"), ("Xu", "Houguo")], 2020, "10.1111/anu.12976", None),
    6: ([("Fuentes", "E."), ("Einarsdóttir", "I."), ("Paredes", "R.")], 2015, "10.1016/j.ygcen.2014.10.012", None),
    7: ([("Codina", "M."), ("Montserrat", "N."), ("Navarro", "I."), ("Gutiérrez", "J.")], 2006, None, None),
    8: ([("Fuentes", "E."), ("Björnsson", "B."), ("Valdés", "J.")], 2011,
        "10.1152/ajpregu.00535.2010", "10.1152/ajpregu. 00535.2010"),
    9: ([("Nornberg", "B."), ("Figueiredo", "M."), ("Marins", "L. F.")], 2016, "10.1016/j.ygcen.2015.12.017", None),
    10: ([("Zhu", "Xin"), ("Ren", "Linrui"), ("Liu", "Jingjie")], 2021, "10.1016/j.aqrep.2021.100838", None),
}


def _declared():
    return {d.reference_id: d for d in build_declared_references(ELICIT_REFERENCES)}


@pytest.mark.parametrize("number", sorted(EXPECTED))
def test_name_first_authors_year_and_doi(number):
    authors, year, doi, written = EXPECTED[number]
    ref = _declared()[number]
    assert [(a.family, a.given) for a in ref.authors] == authors
    assert ref.year == year
    assert ref.doi == doi
    assert ref.doi_as_written == written


def test_et_al_still_marks_truncation():
    declared = _declared()
    assert declared[1].authors_truncated is True
    assert declared[2].authors_truncated is False


def test_apa_family_first_lists_are_unchanged():
    ref = parse_reference_entry(
        1,
        "Lien, S., Koop, B. F., Davidson, W. S. (2016). The Atlantic salmon genome provides insights "
        "into rediploidization. Nature, 533(7602), 200-205. https://doi.org/10.1038/nature17164",
    )
    assert [(a.family, a.given) for a in ref.authors] == [("Lien", "S."), ("Koop", "B. F."), ("Davidson", "W. S.")]
    assert ref.year == 2016 and ref.journal == "Nature" and ref.volume == "533"
    assert ref.doi == "10.1038/nature17164" and ref.doi_as_written is None
    ref = parse_reference_entry(1, "Cleveland, B. M., & Weber, G. M. (2010). Effects. Am J Physiol, 298(2), R341.")
    assert [(a.family, a.given) for a in ref.authors] == [("Cleveland", "B. M."), ("Weber", "G. M.")]


def test_year_with_stray_spaces_inside_parentheses():
    assert parse_reference_entry(1, "Cleveland, B. M., & Weber, G. M. (2010 ). Effects.").year == 2010
    assert parse_reference_entry(1, "B. Nornberg, M. Figueiredo ( 2016) Expression.").year == 2016


def test_name_first_particles():
    ref = parse_reference_entry(1, "Juan Carlos de la Cruz, Ludwig van Beethoven (2019) Title. Journal.")
    assert [(a.family, a.given) for a in ref.authors] == [("de la Cruz", "Juan Carlos"), ("van Beethoven", "Ludwig")]


def test_split_doi_is_rejoined_only_on_the_same_line():
    assert find_doi("https://doi.org/10.1016/j.ygcen. 2008.04.009") == (
        "10.1016/j.ygcen.2008.04.009", "10.1016/j.ygcen. 2008.04.009")
    # A stray page number after a complete DOI is not glued to it.
    assert find_doi("https://doi.org/10.1016/j.ygcen.2014.10.012 11") == ("10.1016/j.ygcen.2014.10.012", None)
    # A DOI ending one line is never glued to the next entry's number.
    assert find_doi("https://doi.org/10.1038/x17164.\n2. B. Cleveland") == ("10.1038/x17164", None)
    assert find_doi("10.1038/x17164. 2 B.") == ("10.1038/x17164", None)
    assert extract_doi("no identifier") is None


def test_content_pipeline_reads_the_same_doi():
    line = ELICIT_REFERENCES[0]["raw"]
    assert _extract_doi_from_line(line) == "10.1016/j.ygcen.2008.04.009"
    assert _extract_doi_from_line(ELICIT_REFERENCES[7]["raw"]) == "10.1152/ajpregu.00535.2010"


def test_check_17_reports_the_doi_as_written():
    declared = list(_declared().values())
    findings = check_17_doi_whitespace(MetadataDoc(sentences=[], body_text=""), declared, {})
    assert sorted((f.reference_id, f.declared, f.actual) for f in findings) == [
        (1, "10.1016/j.ygcen. 2008.04.009", "10.1016/j.ygcen.2008.04.009"),
        (8, "10.1152/ajpregu. 00535.2010", "10.1152/ajpregu.00535.2010"),
    ]
    assert all(f.check_id == 17 and f.field == "doi" for f in findings)


def test_split_doi_becomes_one_readable_client_card():
    finding = check_17_doi_whitespace(MetadataDoc(sentences=[], body_text=""), [_declared()[1]], {})[0]
    entry = {
        "reference_id": 1,
        "status": "miscited",
        "usable_identifier": "10.1016/j.ygcen.2008.04.009",
        "findings": [finding.to_dict()],
    }
    decision = classify_reference(entry, declared_doi="10.1016/j.ygcen.2008.04.009")
    assert decision.reviewed and decision.show_content
    assert [c.error_type for c in decision.cards] == ["wrong_reference_details"]
    shown = decision.cards[0].findings
    assert len(shown) == 1
    assert _readable_field(shown[0]) == (
        "The correct doi is 10.1016/j.ygcen.2008.04.009, not 10.1016/j.ygcen. 2008.04.009.")


def test_crossref_title_markup_is_not_a_title_difference():
    record = ("Fish protein hydrolysate affected amino acid absorption and related gene expressions of "
              "IGF‐1/AKT pathways in turbot (\n            <i>Scophthalmus maximus</i>\n            )")
    declared = _declared()[5].title
    assert clean_crossref_text(record).endswith("in turbot (Scophthalmus maximus)")
    assert title_similarity(declared, record) == 1.0
    assert clean_crossref_text("A &amp; B") == "A & B"
    assert clean_crossref_text(None) is None
