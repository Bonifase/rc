import responses

from src.content_fetcher import _normalize_for_match, fetch_content, gold_sentence_present
from src.config import NCBI_EUTILS_BASE_URL

TEST_EMAIL = "test@example.com"
EFETCH_URL = f"{NCBI_EUTILS_BASE_URL}/efetch.fcgi"

JATS_WITH_BODY = b"""<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <body>
      <p>This is the first paragraph of the actual paper.</p>
      <p>This is the second paragraph with more detail.</p>
    </body>
  </article>
</pmc-articleset>"""

JATS_WITHOUT_BODY = b"""<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <front><article-meta><title-group><article-title>Title Only</article-title></title-group></article-meta></front>
  </article>
</pmc-articleset>"""

PUBMED_WITH_ABSTRACT = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <Article>
        <Abstract>
          <AbstractText>This study examines an important question.</AbstractText>
        </Abstract>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""

PUBMED_NO_ABSTRACT = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <Article><ArticleTitle>Just a title, no abstract</ArticleTitle></Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""

JATS_MULTI_ABSTRACT = b"""<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <front><article-meta>
      <title-group><article-title>Molecular Mechanisms Regulating Muscle Plasticity in Fish</article-title></title-group>
      <abstract>
        <sec>
          <title>Simple Summary</title>
          <p>Fish can remodel their muscles in response to environmental and nutritional cues.</p>
        </sec>
        <sec>
          <title>Abstract</title>
          <p>Muscle plasticity is regulated by molecular mechanisms that this study examines.</p>
        </sec>
      </abstract>
    </article-meta></front>
    <body>
      <sec>
        <title>1. Introduction</title>
        <p>This is a body paragraph, distinct from the abstract.</p>
      </sec>
    </body>
  </article>
</pmc-articleset>"""

JATS_NESTED_ABSTRACT = b"""<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <front><article-meta>
      <abstract>
        <title>Abstract</title>
        <p>Outer paragraph.</p>
        <sec>
          <title>Background</title>
          <p>Background paragraph.</p>
          <sec>
            <title>Nested Sub-section</title>
            <p>Deeply nested paragraph.</p>
          </sec>
        </sec>
      </abstract>
    </article-meta></front>
    <body>
      <p>Body paragraph.</p>
    </body>
  </article>
</pmc-articleset>"""

# A <table-wrap> whose abbreviations/methods notes live in <table-wrap-foot>,
# OUTSIDE the <table> element. A <p>-only walk drops them silently.
JATS_TABLE_FOOTER = b"""<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <body>
      <sec>
        <title>4. Table</title>
        <p>Summary table.</p>
        <table-wrap id="t1">
          <label>Table 1</label>
          <caption><p>Summary of body measures.</p></caption>
          <table>
            <thead><tr><th>Abbr</th><th>Full name</th></tr></thead>
            <tbody><tr><td>mafbx</td><td>muscle atrophy F-box</td></tr></tbody>
          </table>
          <table-wrap-foot>
            <fn><p>mafbx, muscle atrophy F-box; murf1, muscle RING-finger protein-1</p></fn>
          </table-wrap-foot>
        </table-wrap>
      </sec>
    </body>
  </article>
</pmc-articleset>"""

# Back-matter containers with headings, abbreviation definitions, and
# supplementary-material attachments -- all previously flattened or dropped.
JATS_BACK_STRUCTURE = b"""<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <body><p>Body paragraph.</p></body>
    <back>
      <fn-group>
        <title>Declarations</title>
        <fn><p>No competing interests.</p></fn>
      </fn-group>
      <notes><title>Institutional Review Board Statement</title><p>The study was approved.</p></notes>
      <ack><title>Acknowledgments</title><p>We thank the facility staff.</p></ack>
      <glossary>
        <title>Abbreviations</title>
        <def-list>
          <def-item><term>mafbx</term><def><p>muscle atrophy F-box</p></def></def-item>
          <def-item><term>murf1</term><def><p>muscle RING-finger protein-1</p></def></def-item>
        </def-list>
      </glossary>
      <supplementary-material>
        <label>Supplementary Material</label>
        <caption><title>Supplemental_Table_S5.xlsx</title></caption>
        <object-id pub-id-type="file">Supp_Table_S5.xlsx</object-id>
      </supplementary-material>
      <ref-list>
        <title>References</title>
        <ref><label>1.</label><mixed-citation>Doe J. A study. 2020.</mixed-citation></ref>
      </ref-list>
    </back>
  </article>
</pmc-articleset>"""

# A <p> that embeds a <boxed-text> inline (Taylor & Francis style). Must be
# split into prose / [Box] block / prose, not flattened into a run-on.
JATS_BOXED_INLINE = b"""<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <body>
      <sec>
        <title>5. Boxes</title>
        <p>Here we summarize the main computational tasks.
          <boxed-text id="box1">
            <label>Box 1</label>
            <caption><title>Key computational tasks</title></caption>
            <p>Co-registration of modalities is performed with spatial transcriptomics.</p>
            <p>Deconvolution is applied to the full dataset.</p>
          </boxed-text>
          The following sections describe each task in turn.
        </p>
      </sec>
    </body>
  </article>
</pmc-articleset>"""

# Reference lists are intentionally pruned; their titles must not become
# headings and their citations must not leak into the fetched text.
JATS_REF_PRUNE = b"""<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <body>
      <sec>
        <title>2. Methods</title>
        <p>We used previously published methods.</p>
      </sec>
    </body>
    <back>
      <ref-list>
        <title>References</title>
        <ref><label>1.</label><mixed-citation>Doe J. A study. 2020.</mixed-citation></ref>
        <ref><label>2.</label><mixed-citation>Roe R. Another study. 2021.</mixed-citation></ref>
      </ref-list>
    </back>
  </article>
</pmc-articleset>"""


@responses.activate
def test_fetch_content_full_text_success():
    responses.add(responses.GET, EFETCH_URL, body=JATS_WITH_BODY, status=200)

    result = fetch_content(pmid="123", pmcid="PMC9876543", contact_email=TEST_EMAIL)

    assert result.status == "full_text"
    assert result.source == "pmc"
    assert "first paragraph" in result.text
    assert "second paragraph" in result.text


@responses.activate
def test_fetch_content_pmcid_exists_but_no_body_falls_back_to_abstract():
    # The exact gap the guide flags: PMCID present, but EFetch doesn't return
    # retrievable full text (open-access status dependent). Must fall back,
    # not error out or silently return nothing.
    responses.add(responses.GET, EFETCH_URL, body=JATS_WITHOUT_BODY, status=200)  # PMC call
    responses.add(responses.GET, EFETCH_URL, body=PUBMED_WITH_ABSTRACT, status=200)  # PubMed fallback

    result = fetch_content(pmid="123", pmcid="PMC9876543", contact_email=TEST_EMAIL)

    assert result.status == "abstract_only"
    assert result.source == "pubmed"
    assert "important question" in result.text
    assert len(responses.calls) == 2


@responses.activate
def test_fetch_content_no_pmcid_goes_straight_to_abstract():
    responses.add(responses.GET, EFETCH_URL, body=PUBMED_WITH_ABSTRACT, status=200)

    result = fetch_content(pmid="123", pmcid=None, contact_email=TEST_EMAIL)

    assert result.status == "abstract_only"
    assert result.source == "pubmed"
    assert len(responses.calls) == 1  # never tried PMC at all


@responses.activate
def test_fetch_content_neither_id_is_unavailable_no_network_call():
    result = fetch_content(pmid=None, pmcid=None, contact_email=TEST_EMAIL)

    assert result.status == "unavailable"
    assert len(responses.calls) == 0


@responses.activate
def test_fetch_content_pmcid_no_body_and_pubmed_no_abstract_is_unavailable():
    responses.add(responses.GET, EFETCH_URL, body=JATS_WITHOUT_BODY, status=200)
    responses.add(responses.GET, EFETCH_URL, body=PUBMED_NO_ABSTRACT, status=200)

    result = fetch_content(pmid="123", pmcid="PMC9876543", contact_email=TEST_EMAIL)

    assert result.status == "unavailable"


@responses.activate
def test_fetch_content_retries_on_500_then_succeeds():
    responses.add(responses.GET, EFETCH_URL, status=500)
    responses.add(responses.GET, EFETCH_URL, status=500)
    responses.add(responses.GET, EFETCH_URL, body=JATS_WITH_BODY, status=200)

    result = fetch_content(pmid=None, pmcid="PMC9876543", contact_email=TEST_EMAIL)

    assert result.status == "full_text"
    assert len(responses.calls) == 3


@responses.activate
def test_fetch_content_exhausts_retries_and_reports_error():
    for _ in range(3):
        responses.add(responses.GET, EFETCH_URL, status=503)

    result = fetch_content(pmid=None, pmcid="PMC9876543", contact_email=TEST_EMAIL)

    assert result.status == "error"


@responses.activate
def test_fetch_content_multiple_abstract_sections_preserved():
    # EF-001 regression: a single <abstract> with two <sec>s ("Simple Summary"
    # and "Abstract") must render both section titles and their paragraphs,
    # with the paragraphs NOT duplicated between sub-sections or the body.
    responses.add(responses.GET, EFETCH_URL, body=JATS_MULTI_ABSTRACT, status=200)

    result = fetch_content(pmid=None, pmcid="PMC7824542", contact_email=TEST_EMAIL)

    assert result.status == "full_text"
    assert "Molecular Mechanisms Regulating Muscle Plasticity in Fish" in result.text
    assert "## Simple Summary" in result.text
    assert "## Abstract" in result.text
    assert "Fish can remodel their muscles in response to" in result.text
    assert "molecular mechanisms that this study examines" in result.text
    # Distinct, non-duplicated: each abstract sentence appears exactly once and
    # separately from the body paragraph.
    assert result.text.count("Fish can remodel their muscles in response to") == 1
    assert result.text.count("molecular mechanisms that this study examines") == 1
    assert "distinct from the abstract" in result.text
    assert result.text.count("distinct from the abstract") == 1


@responses.activate
def test_fetch_content_nested_abstract_sections_recursive():
    # Standard-flat abstract plus arbitrarily nested <sec>s must keep every
    # title and paragraph in document order, regardless of nesting depth.
    responses.add(responses.GET, EFETCH_URL, body=JATS_NESTED_ABSTRACT, status=200)

    result = fetch_content(pmid=None, pmcid="PMC1234567", contact_email=TEST_EMAIL)

    assert result.status == "full_text"
    title_idx = result.text.index("## Abstract")
    bg_idx = result.text.index("## Background")
    sub_idx = result.text.index("## Nested Sub-section")
    assert title_idx < bg_idx < sub_idx
    assert "Outer paragraph" in result.text
    assert "Background paragraph" in result.text
    assert "Deeply nested paragraph" in result.text
    # No duplication across nesting levels.
    assert result.text.count("Outer paragraph") == 1
    assert result.text.count("Background paragraph") == 1
    assert result.text.count("Deeply nested paragraph") == 1
    assert "Body paragraph" in result.text


@responses.activate
def test_fetch_content_table_wrap_foot_is_kept():
    # Regression (real 27/27 gap): table footnote / abbreviation lines live in
    # <table-wrap-foot>, outside the <table>. They must not be dropped.
    responses.add(responses.GET, EFETCH_URL, body=JATS_TABLE_FOOTER, status=200)

    result = fetch_content(pmid=None, pmcid="PMC1111111", contact_email=TEST_EMAIL)

    assert result.status == "full_text"
    assert "[Table] Table 1 Summary of body measures." in result.text
    assert "[Footnote] mafbx, muscle atrophy F-box; murf1, muscle RING-finger protein-1" in result.text


@responses.activate
def test_fetch_content_back_matter_headings_and_definitions():
    # Regression (real 15-title-line gap + abbreviations + attachments):
    # fn-group/notes/ack headings become "## " lines, glossary def-items keep
    # "term: definition", and supplementary-material labels are one line.
    responses.add(responses.GET, EFETCH_URL, body=JATS_BACK_STRUCTURE, status=200)

    result = fetch_content(pmid=None, pmcid="PMC2222222", contact_email=TEST_EMAIL)

    assert result.status == "full_text"
    assert "## Declarations" in result.text
    assert "## Institutional Review Board Statement" in result.text
    assert "## Acknowledgments" in result.text
    assert "## Abbreviations" in result.text
    assert "mafbx: muscle atrophy F-box" in result.text
    assert "murf1: muscle RING-finger protein-1" in result.text
    assert "[Supplementary] Supplementary Material Supplemental_Table_S5.xlsx" in result.text
    # No duplication: each heading appears exactly once.
    assert result.text.count("## Declarations") == 1
    assert result.text.count("## Institutional Review Board Statement") == 1


@responses.activate
def test_fetch_content_boxed_text_inline_becomes_box_block():
    # Regression (SO-011): a <boxed-text> nested inside a <p> must be split
    # into prose / "[Box] label + caption title" / prose, not flattened.
    responses.add(responses.GET, EFETCH_URL, body=JATS_BOXED_INLINE, status=200)

    result = fetch_content(pmid=None, pmcid="PMC3333333", contact_email=TEST_EMAIL)

    assert result.status == "full_text"
    assert "Here we summarize the main computational tasks." in result.text
    assert "[Box] Box 1 Key computational tasks" in result.text
    assert "\n\nCo-registration of modalities is performed with spatial transcriptomics.\n\nDeconvolution is applied to the full dataset.\n\nThe following sections describe each task in turn." in result.text


@responses.activate
def test_fetch_content_reference_list_pruned():
    # References are intentionally NOT part of the fetched text: the ref-list
    # title must not become a heading and citations must not leak.
    responses.add(responses.GET, EFETCH_URL, body=JATS_REF_PRUNE, status=200)

    result = fetch_content(pmid=None, pmcid="PMC4444444", contact_email=TEST_EMAIL)

    assert result.status == "full_text"
    assert "## References" not in result.text
    assert "Doe J. A study. 2020." not in result.text
    assert "Another study. 2021." not in result.text
    assert "previously published methods" in result.text


def test_normalize_for_match_unicode_mapping():
    # Normalization must make publisher/NCBI typography match the gold quote:
    # curly quotes, thin space (U+2009), superscripts, micro sign, dashes.
    gold = _normalize_for_match(
        "Doses of \u00b1100\u2013100\u2009\u00b5g\u2009mL\u207b\u00b9 were used at 5 C."
    )
    fetcher = _normalize_for_match("Doses of +/-100-100 ug mL-1 were used at 5 C.")
    assert gold == fetcher

    assert _normalize_for_match("the\u2019cat\u2019s\u201ccoat\u201d") == "the'cat's\"coat\""
    assert _normalize_for_match("CO\u2082") == "co2"
    assert _normalize_for_match("10\u2075") == "105"
    assert _normalize_for_match("a\u200bb") == "ab"


def test_gold_sentence_present_per_sentence_split():
    # A gold quote the fetched text carries as separate sentences (with a
    # heading in between) is still counted present.
    gold = ("Fish remodel their muscles in response to cues. "
            "Programming is affected by thermal history. This predicts growth.")
    fetched = ("Fish remodel their muscles in response to cues.\n"
               "## Results\n"
               "This predicts growth.\n"
               "The authors note that programming is affected by thermal history.")
    present, ratio, _ = gold_sentence_present(gold, fetched)
    assert present
    assert ratio == 1.0


def test_gold_sentence_present_format_tolerant_substring():
    # An inline element-boundary artifact (a stray hyphen) between sentences
    # must not hide a quote whose word sequence is verbatim present.
    gold = ("Nongenomic signaling is mediated. In addition to effects, "
            "biophysical changes matter.")
    fetched = ("Nongenomic signaling is mediated -. In addition to effects, "
               "biophysical changes matter.")
    present, ratio, how = gold_sentence_present(gold, fetched)
    assert present
    assert ratio == 1.0
    assert "collapsed" in how


def test_gold_sentence_present_paraphrase_still_missing():
    # A semantically similar but NOT verbatim paraphrase must NOT count as
    # present -- only verbatim-ish matching counts.
    gold = "We achieved a 25-fold improvement in neutralization against BQ.1.1."
    fetched = ("Using a structure-informed language model, the authors improved "
               "neutralization potency and resilience by up to 25-fold.")
    present, ratio, _ = gold_sentence_present(gold, fetched)
    assert not present
    assert ratio < 0.9
