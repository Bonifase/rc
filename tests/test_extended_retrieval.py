"""Targeted unit + integration tests for Work A (extended retrieval).

Covers each of Eduardo's six blockers without ever using the permanently
excluded gold cases (IGF-007 / IGF-023 / SB-035):

  #2  _split_abstract_body_loose must not include the abstract in the body.
  #3  final_url + content_hash populated on abstract and full-text results.
  #4a _first_author_family("Jane Smith") == "smith" (family-last handling).
  #4b a candidate without a publication year never passes the title + first
      author + year identity rule.
  #5  multi-article PDFs abstain rather than being treated as one paper's body.
  #6  these tests exercise retrieve_extended() / fetch_source_content()
      end-to-end against mocked network boundaries.

All matching uses synthetic DOIs / titles and never the excluded cases.
"""
from unittest.mock import patch

from src.retrieval import (
    Citation,
    Candidate,
    _first_author_family,
    candidate_identity,
    _split_abstract_body_loose,
    _pdf_multi_article,
    retrieve_extended,
)
from src.content_fetcher import fetch_source_content


# ---------------------------------------------------------------------------
# #4a -- first-author family extraction
# ---------------------------------------------------------------------------

def test_first_author_family_given_family_returns_family():
    assert _first_author_family("Jane Smith") == "smith"


def test_first_author_family_family_comma_given():
    assert _first_author_family("Codina, M. Estefania") == "codina"


def test_first_author_family_multi_token_given_family():
    assert _first_author_family("Marta Codina") == "codina"


def test_first_author_family_single_token():
    assert _first_author_family("Wei") == "wei"


def test_first_author_family_blank():
    assert _first_author_family("") == ""
    assert _first_author_family(None) == ""


# ---------------------------------------------------------------------------
# #4b -- candidate without a publication year never passes the identity rule
# ---------------------------------------------------------------------------

def test_candidate_identity_requires_candidate_year_when_declared():
    citation = Citation(title="On the Growth of Coral", first_author="Jane Smith",
                        publication_year=2020)
    cand = Candidate(source="crossref", title="On the Growth of Coral",
                     authors=["Jane Smith"], year=None)
    ok, note = candidate_identity(citation, cand)
    assert ok is False
    assert "missing publication year" in note


def test_candidate_identity_matching_year_passes():
    citation = Citation(title="On the Growth of Coral", first_author="Jane Smith",
                        publication_year=2020)
    cand = Candidate(source="crossref", title="On the Growth of Coral",
                     authors=["Jane Smith"], year=2020)
    ok, _ = candidate_identity(citation, cand)
    assert ok is True


def test_candidate_identity_conflicting_year_fails():
    citation = Citation(title="On the Growth of Coral", first_author="Jane Smith",
                        publication_year=2020)
    cand = Candidate(source="crossref", title="On the Growth of Coral",
                     authors=["Jane Smith"], year=2019)
    ok, note = candidate_identity(citation, cand)
    assert ok is False
    assert "year conflict" in note


# ---------------------------------------------------------------------------
# #2 -- abstract/body split must not let the abstract leak into the body
# ---------------------------------------------------------------------------

def test_split_abstract_body_loose_body_starts_after_abstract():
    text = (
        "On the Growth of Coral\n"
        "Jane Smith, 2020\n"
        "Abstract\n"
        "We study reef growth over ten years across three sites.\n"
        "1. Introduction\n"
        "Coral reefs are productive ecosystems.\n"
        "2. Methods\n"
        "We sampled monthly.\n"
    )
    abstract, body, status = _split_abstract_body_loose(text)
    assert status == "resolved"
    assert abstract and "reef growth" in abstract
    assert body is not None
    # The abstract heading + its text must never appear in the body.
    assert "Abstract" not in body
    assert "reef growth over ten years" not in body
    # The body starts at the Introduction and includes later sections.
    assert "Introduction" in body
    assert "Methods" in body


def test_split_abstract_body_loose_no_header_is_unresolved():
    text = "Just a blob of text without any labeled abstract or sections."
    abstract, body, status = _split_abstract_body_loose(text)
    assert status == "unresolved"
    assert abstract is None
    assert body is not None


# ---------------------------------------------------------------------------
# #5 -- multi-article PDF detection
# ---------------------------------------------------------------------------

def test_pdf_multi_article_flags_two_article_structures():
    text = (
        "Article One\nAbstract\nFirst abstract text.\n"
        + ("body text " * 350)
        + "\nReferences\nReference 1. doi:10.1000/a\n"
        + ("reference text " * 80)
        + "\nArticle Two\nAbstract\nSecond abstract text.\n"
        + ("second body " * 100)
    )
    assert _pdf_multi_article(text) is True


def test_pdf_single_article_reference_list_many_dois_is_not_multi():
    text = (
        "A Normal Research Article\nAbstract\nWe report a result.\n"
        + ("main article body " * 300)
        + "\nReferences\n"
        + "1. Alpha et al. doi:10.1000/ref.a PMID: 12345678\n"
        + "2. Beta et al. doi:10.1000/ref.b PMID: 23456789\n"
        + "3. Gamma et al. https://doi.org/10.1000/ref.c 37442136\n"
    )
    assert _pdf_multi_article(text) is False


def test_pdf_multi_article_no_structure_is_not_multi():
    assert _pdf_multi_article("Just plain prose with no article-level structure.") is False


# ---------------------------------------------------------------------------
# #3 -- final_url + content_hash populated on full-text retrieval
# ---------------------------------------------------------------------------

@patch("src.retrieval._semanticscholar_by_doi", return_value=None)
@patch("src.retrieval._crossref_by_doi", return_value=None)
@patch("src.retrieval._openalex_by_doi", return_value=None)
@patch("src.retrieval._fetch_pmc")
def test_retrieve_extended_pmc_full_text_sets_url_and_hash(mock_pmc, *_mocks):
    mock_pmc.return_value = (
        "The abstract text",
        "The full body text with plenty of words.",
        "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123",
        "deadbeefdeadbeef",
    )
    res = retrieve_extended(Citation(doi="10.9999/pmc", pmcid="PMC123"))
    assert res.status == "full_text"
    assert res.retrieval_source == "pmc"
    assert res.final_url == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123"
    assert res.content_hash == "deadbeefdeadbeef"


@patch("src.retrieval._semanticscholar_by_doi", return_value=None)
@patch("src.retrieval._crossref_by_doi", return_value=None)
@patch("src.retrieval._openalex_by_doi", return_value=None)
@patch("src.retrieval._fetch_pmc")
def test_retrieve_extended_europepmc_full_text_sets_url_and_hash(mock_pmc, *_mocks):
    epmc_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC999/fullTextXML"
    # PMC JATS has no body here, so the cascade must fall through to the
    # Europe PMC fullTextXML path -- the exact branch Eduardo flagged as
    # leaving the hash empty.
    mock_pmc.return_value = (None, None, "", "")
    with patch("src.retrieval._fetch_europepmc_fulltext",
               return_value=("The abstract text", "The full body text with plenty of words.",
                             epmc_url, "eupmcdeadbeef")):
        res = retrieve_extended(Citation(doi="10.9999/eupmc", pmcid="PMC999"))
    assert res.status == "full_text"
    assert res.retrieval_source == "europepmc"
    assert res.retrieval_status == "full"
    assert res.structure_status == "resolved"
    assert res.final_url == epmc_url
    assert res.content_hash == "eupmcdeadbeef"


@patch("src.retrieval._semanticscholar_by_doi", return_value=None)
@patch("src.retrieval._crossref_by_doi", return_value=None)
@patch("src.retrieval._openalex_by_doi", return_value=None)
@patch("src.retrieval._fetch_pmc")
def test_retrieve_extended_europepmc_abstract_only_sets_url_and_hash(mock_pmc, *_mocks):
    epmc_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC888/fullTextXML"
    mock_pmc.return_value = (None, None, "", "")
    with patch("src.retrieval._fetch_europepmc_fulltext",
               return_value=("A substantial abstract of usable length for the model.",
                             None, epmc_url, "eupmcabhash")):
        res = retrieve_extended(Citation(doi="10.9999/eupmc-abs", pmcid="PMC888"))
    assert res.status == "abstract_only"
    assert res.retrieval_source == "europepmc"
    assert res.structure_status == "not_applicable"
    assert res.final_url == epmc_url
    assert res.content_hash == "eupmcabhash"


@patch("src.retrieval._semanticscholar_by_doi", return_value=None)
@patch("src.retrieval._crossref_by_doi", return_value=None)
@patch("src.retrieval._openalex_by_doi", return_value=None)
def test_fetch_source_content_pmc_populates_hash(mock_oa, mock_cr, mock_ss):
    from src.retrieval import _fetch_pmc
    with patch("src.retrieval._fetch_pmc",
               return_value=("A", "B", "https://pmc/x", "hashval123")):
        cr = fetch_source_content(doi="10.9999/x", pmcid="PMC1")
    assert cr.status == "full_text"
    assert cr.content_hash == "hashval123"
    assert cr.final_url == "https://pmc/x"


@patch("src.retrieval._semanticscholar_by_doi", return_value=None)
@patch("src.retrieval._crossref_by_doi", return_value=None)
@patch("src.retrieval._openalex_by_doi", return_value=None)
def test_fetch_source_content_aggregator_abstract_sets_url_and_hash(_a, _b, _c):
    mock_candidate = Candidate(
        source="crossref", doi="10.9999/abs",
        title="A Synthetic Abstract Paper", authors=["Jane Smith"], year=2022,
        abstract="A sufficiently long abstract describing the key findings.",
    )
    with patch("src.retrieval._crossref_by_doi", return_value=mock_candidate), \
         patch("src.retrieval._fetch_pmc") as mock_pmc, \
         patch("src.retrieval._unpaywall_locations", return_value=[]):
        mock_pmc.return_value = (None, "", "", "")
        cr = fetch_source_content(doi="10.9999/abs")
    assert cr.status == "abstract_only"
    assert cr.final_url
    assert cr.content_hash  # metadata-abstract hash must be non-empty


# ---------------------------------------------------------------------------
# #5 -- multi-article PDF abstention end-to-end through the OA path
# ---------------------------------------------------------------------------

@patch("src.retrieval._semanticscholar_by_doi", return_value=None)
@patch("src.retrieval._openalex_by_doi", return_value=None)
def test_retrieve_extended_abstains_on_multi_article_pdf_when_no_safe_fallback(mock_oa, mock_ss):
    candidate = Candidate(
        source="crossref", doi="10.9999/multi",
        title="A Compiled Conference Volume", authors=["Jane Smith"], year=2021,
    )
    with patch("src.retrieval._crossref_by_doi", return_value=candidate), \
         patch("src.retrieval._unpaywall_locations",
               return_value=[{"url_for_pdf": "https://example/volume.pdf",
                              "url": "https://example/volume",
                              "url_for_landing_page": "https://example/volume",
                              "is_oa": True}]), \
         patch("src.retrieval._fetch_open_access",
               return_value=("", "https://example/volume.pdf", "hashM1", True)):
        res = retrieve_extended(Citation(doi="10.9999/multi"))

    assert res.status == "abstained"
    assert res.retrieval_status == "abstained"
    assert "Multi-article" in (res.abstained_reason or "")


@patch("src.retrieval._semanticscholar_by_doi", return_value=None)
@patch("src.retrieval._openalex_by_doi", return_value=None)
def test_retrieve_extended_multi_pdf_falls_back_to_aggregator_abstract(mock_oa, mock_ss):
    candidate = Candidate(
        source="crossref", doi="10.9999/multi-abs",
        title="A Target Article", authors=["Jane Smith"], year=2021,
        abstract="A safe abstract that remains usable when the located PDF is a bundle.",
    )
    with patch("src.retrieval._crossref_by_doi", return_value=candidate), \
         patch("src.retrieval._unpaywall_locations",
               return_value=[{"url_for_pdf": "https://example/volume.pdf",
                              "url": "https://example/volume",
                              "url_for_landing_page": "https://example/volume",
                              "is_oa": True}]), \
         patch("src.retrieval._fetch_open_access",
               return_value=("", "https://example/volume.pdf", "hashM2", True)):
        res = retrieve_extended(Citation(doi="10.9999/multi-abs"))

    assert res.status == "abstract_only"
    assert res.retrieval_status == "abstract_only"
    assert res.abstract_text == candidate.abstract


@patch("src.retrieval._semanticscholar_by_doi", return_value=None)
@patch("src.retrieval._openalex_by_doi", return_value=None)
def test_retrieve_extended_multi_pdf_tries_next_oa_location(mock_oa, mock_ss):
    candidate = Candidate(
        source="crossref", doi="10.9999/multi-next",
        title="A Target Article", authors=["Jane Smith"], year=2021,
    )
    locations = [
        {"url_for_pdf": "https://example/bundle.pdf", "url": "https://example/bundle", "is_oa": True},
        {"url_for_pdf": "https://example/article.pdf", "url": "https://example/article", "is_oa": True},
    ]
    safe_text = ("A Target Article Jane Smith 2021\nAbstract\nA result.\n"
                 "Introduction\n" + ("safe body text " * 80))
    with patch("src.retrieval._crossref_by_doi", return_value=candidate), \
         patch("src.retrieval._unpaywall_locations", return_value=locations), \
         patch("src.retrieval._fetch_open_access",
               side_effect=[("", "https://example/bundle.pdf", "hashM3", True),
                            (safe_text, "https://example/article.pdf", "hashM4", False)]):
        res = retrieve_extended(Citation(doi="10.9999/multi-next",
                                         title="A Target Article",
                                         first_author="Jane Smith",
                                         publication_year=2021))

    assert res.status == "full_text"
    assert res.final_url == "https://example/article.pdf"
    assert res.content_hash == "hashM4"


# ---------------------------------------------------------------------------
# #1 -- the pipeline passes Crossref metadata into the extended content fetch
# ---------------------------------------------------------------------------

@patch("src.retrieval._semanticscholar_by_doi", return_value=None)
@patch("src.retrieval._crossref_by_doi", return_value=None)
@patch("src.retrieval._openalex_by_doi", return_value=None)
def test_retrieve_extended_title_author_year_flow(mock_oa, mock_cr, mock_ss):
    citation = Citation(doi="10.9999/title", title="A Known Title",
                        first_author="Jane Smith", publication_year=2020)
    candidate = Candidate(source="crossref", doi="10.9999/title",
                          title="A Known Title", authors=["Jane Smith"],
                          year=2020, abstract="A usable synthetic abstract of adequate length.")
    with patch("src.retrieval._crossref_by_doi", return_value=candidate), \
         patch("src.retrieval._unpaywall_locations", return_value=[]):
        res = retrieve_extended(citation)
    # A passing single candidate reaches the content stage (not an abstention).
    assert res.status in ("abstract_only", "full_text", "not_found")
    assert res.abstained_reason is None
