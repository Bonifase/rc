import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "index.html"
JOB = "4" * 32


def _content(ref, claim="A claim."):
    return {"reference_id": ref, "marker": f"[{ref}]", "kind": "content", "source_label": f"Ref {ref}",
            "error_type": "data_mismatch", "claim": claim, "evidence": "Evidence.", "reference": f"Reference {ref}",
            "observation": "Obs.", "passage_origin": "abstract"}


def _metadata(ref):
    return {"reference_id": ref, "marker": f"[{ref}]", "kind": "metadata", "source_label": f"Ref {ref}",
            "error_type": "wrong_reference_details", "claim": "A claim.", "reference_as_generated": "As written",
            "reference_official": "Official", "findings": ["The correct year is 2020, not 2019."], "observation": "Obs."}


def _verification(ref):
    return {"reference_id": ref, "marker": f"[{ref}]", "kind": "verification", "source_label": f"Ref {ref}",
            "error_type": "full_text_check_recommended", "claim": "A claim.", "reference": f"Reference {ref}",
            "observation": "Only the abstract of the cited source was available. A full-text check is recommended."}


def _summarize(report):
    html = TEMPLATE.read_text(encoding="utf-8")
    start = html.index("function distinctReferenceCount(cases)")
    end = html.index("function renderAnalysisDesign(report, fileName)")
    script = html[start:end] + "\nconsole.log(JSON.stringify(summarizeReport(JSON.parse(process.argv[1]))));"
    result = subprocess.run(["node", "-e", script, json.dumps(report)], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is required to run the template's counting code")


@needs_node
def test_citation_errors_require_verification_and_metadata_problems_are_three_separate_numbers():
    report = {"references_reviewed": 5, "cases": [_content(1), _metadata(2), _metadata(4)], "verification_cases": [_verification(3)]}

    summary = _summarize(report)

    assert (summary["citationErrors"], summary["requireVerification"], summary["metadataProblems"]) == (1, 1, 2)


@needs_node
def test_every_counter_counts_distinct_references_not_cards():
    report = {
        "references_reviewed": 6,
        "cases": [_content(1, "one"), _content(1, "two"), _content(1, "three"), _content(2), _metadata(3), _metadata(3)],
        "verification_cases": [_verification(4), _verification(4)],
    }

    summary = _summarize(report)

    assert (summary["citationErrors"], summary["requireVerification"], summary["metadataProblems"]) == (2, 1, 1)
    assert len(summary["cases"]) == 6  # every card is still listed


@needs_node
def test_orphan_citations_are_not_counted_as_references():
    orphan = {**_metadata("orphan:9"), "error_type": "citation_without_reference", "count_as_reference": False}

    assert _summarize({"references_reviewed": 3, "cases": [_metadata(1), orphan]})["metadataProblems"] == 1


@needs_node
def test_reviewed_count_is_the_reported_value_and_never_inflated_by_the_counters():
    cases = [_content(1), _content(2), _content(3), _metadata(4)]

    assert _summarize({"references_reviewed": 2, "cases": cases})["reviewed"] == 2
    assert _summarize({"references_reviewed": 9, "cases": cases})["reviewed"] == 9
    assert _summarize({"cases": cases, "references": [{"number": n} for n in range(1, 8)]})["reviewed"] == 7


@needs_node
@pytest.mark.parametrize("status,ran", [("not_executed", False), ("failed", False), ("executed", True), ("simulated", True), (None, True)])
def test_judge_ran_flag_follows_the_judge_status(status, ran):
    report = {"references_reviewed": 1, "cases": [_metadata(1)]}
    if status:
        report["judge_status"] = status

    assert _summarize(report)["judgeRan"] is ran


def test_sentence_hides_the_citation_counters_when_the_judge_did_not_run():
    html = TEMPLATE.read_text(encoding="utf-8")
    render = html.split("function renderAnalysisDesign(report, fileName)", 1)[1].split("function renderResult", 1)[0]

    assert "summary.judgeRan" in render
    assert "citationStat.hidden = !summary.judgeRan" in render
    assert "Errors detected" not in html.split("function summarizeReport", 1)[1]


# ---- rendered page (needs Playwright; skipped where it is not installed) ----

def _page(playwright):
    try:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
    except Exception as exc:  # browser binary missing
        pytest.skip(f"Chromium is not available: {exc}")
    html = TEMPLATE.read_text(encoding="utf-8")
    html = re.sub(r'(<script id="report-pdf"[^>]*>).*?(</script>)', r"\1\2", html, flags=re.S)
    html = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/Sc5aaQAAAABJRU5ErkJggg==", html)
    page = browser.new_page()
    page.set_content(html, wait_until="load")
    return browser, page


def _render(page, report):
    page.evaluate(
        """({job, response}) => { window.fetch = async (url) => String(url).includes('/api/upload-manuscript')
          ? {ok: true, status: 202, json: async () => ({job_id: job})}
          : {ok: true, status: 200, json: async () => response}; }""",
        {"job": JOB, "response": {"status": "done", "report": report}},
    )
    page.locator("#manuscript-file").set_input_files({"name": "m.docx", "mimeType": "application/octet-stream", "buffer": b"x"})
    page.locator("#submit-button").click()
    page.wait_for_function("document.getElementById('upload-status').textContent.includes('Analysis complete')", timeout=7000)
    return page.locator("#verdict").inner_text()


def test_rendered_sentence_shows_three_separate_counters_with_the_real_reviewed_count():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        browser, page = _page(playwright)
        try:
            sentence = _render(page, {"references_reviewed": 7, "judge_status": "executed",
                                      "cases": [_content(1), _metadata(2)], "verification_cases": [_verification(3)]})
        finally:
            browser.close()

    assert sentence == "7 references were reviewed. Citation errors: 1. Require verification: 1. Metadata problems: 1."


def test_rendered_counters_count_references_while_every_card_stays_visible():
    sync_api = pytest.importorskip("playwright.sync_api")
    cases = [_content(1, "first"), _content(1, "second"), _content(1, "third"), _metadata(1)]
    with sync_api.sync_playwright() as playwright:
        browser, page = _page(playwright)
        try:
            sentence = _render(page, {"references_reviewed": 4, "judge_status": "executed", "cases": cases})
            cards = page.locator("#list .case").count()
            card_numbers = (page.locator("#n-content").inner_text(), page.locator("#n-meta").inner_text())
        finally:
            browser.close()

    assert sentence == "4 references were reviewed. Citation errors: 1. Require verification: 0. Metadata problems: 1."
    assert cards == 4
    assert card_numbers == ("1", "1")


def test_rendered_page_hides_citation_counters_when_the_judge_did_not_run():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        browser, page = _page(playwright)
        try:
            sentence = _render(page, {"references_reviewed": 9, "judge_status": "not_executed", "judge_blocker": "missing_credentials",
                                      "cases": [_metadata(2)]})
            citation_card_visible = page.locator('.stat[data-filter="content"]').is_visible()
            metadata_card_visible = page.locator('.stat[data-filter="metadata"]').is_visible()
        finally:
            browser.close()

    assert sentence.startswith("9 references were reviewed. Metadata problems: 1.")
    assert "Citation errors" not in sentence and "Require verification" not in sentence
    assert citation_card_visible is False
    assert metadata_card_visible is True
