"""
CLI interface. Input is a JSON object with a numbered reference list and a
set of sentences containing bracket citation markers ([1], [2,3], [4-6],
etc.) -- per "Input format for v0" in the integration note: numbered
reference list with a DOI per entry, no author-year or no-DOI entries yet.

An entry may, however, omit the DOI when it carries enough to search by --
either "raw" bibliography text or "title" + "publication_year" (and
optionally "first_author") -- so identifier-free citations reach the
extended-retrieval cascade instead of being dropped.

Raw-text extraction (turning a pasted paper into this sentence/reference
structure) is still on the client's end, not wired up here.

Usage:
    python run.py --input data/sample_input.json --output output/report.json
"""
import argparse
import json
import sys
from pathlib import Path

from src.config import require_contact_email
from src.aggregator import generate_report
from src.analysis_runner import run_content_and_metadata


def load_input(path: Path) -> tuple[dict[int, dict | str], list[dict]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "reference_list" not in data or "sentences" not in data:
        raise ValueError("Input JSON must have top-level 'reference_list' and 'sentences' keys.")

    reference_list: dict[int, dict | str] = {}
    for i, entry in enumerate(data["reference_list"]):
        missing = {"number"} - entry.keys()
        if missing:
            raise ValueError(f"reference_list entry at index {i} is missing: {sorted(missing)}")
        searchable = entry.get("doi") or entry.get("raw") or (entry.get("title") and entry.get("publication_year"))
        if not searchable:
            raise ValueError(
                f"reference_list entry at index {i} is missing a doi (and carries no title + "
                "publication_year or raw bibliography text to search by)."
            )
        try:
            reference_list[int(entry["number"])] = entry
        except (TypeError, ValueError):
            raise ValueError(f"reference_list entry at index {i} has a non-numeric 'number'.")

    sentences = data["sentences"]
    for i, sentence in enumerate(sentences):
        missing = {"id", "text"} - sentence.keys()
        if missing:
            raise ValueError(f"sentences entry at index {i} is missing: {sorted(missing)}")

    return reference_list, sentences


def _short(text: str, n: int = 70) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def print_human_summary(report: dict) -> None:
    summary = report["summary"]
    print(f"\n{summary['total_sentences']} sentences, {summary['total_checks']} checks.\n")

    print("Sentence verdicts:")
    for verdict, count in summary["sentence_verdict_counts"].items():
        print(f"  {verdict}: {count}")

    if summary["pair_status_counts"]:
        print("\nPer-check outcomes (includes non-verdict statuses):")
        for status, count in summary["pair_status_counts"].items():
            print(f"  {status}: {count}")

    bibliography_status = report.get("bibliography_status", [])
    if bibliography_status:
        status_counts: dict[str, int] = {}
        for entry in bibliography_status:
            status_counts[entry["status"]] = status_counts.get(entry["status"], 0) + 1
        print("\nReference metadata status:")
        for status, count in status_counts.items():
            print(f"  {status}: {count}")

    print()
    for sentence in report["sentences"]:
        flag = " [incomplete]" if sentence["has_incomplete_pairs"] else ""
        print(f"- \"{_short(sentence['sentence_text'])}\"")
        print(f"    sentence verdict: {sentence['verdict']}{flag}")
        for pair in sentence["pairs"]:
            if pair["status"] == "verdict":
                print(f"    [{pair['ref_number']}] {pair['verdict']} ({pair['source_type']}) — {_short(pair['explanation'] or '', 90)}")
                if pair["evidence_quote"]:
                    print(f"        \"{_short(pair['evidence_quote'], 100)}\"")
            elif pair["status"] != "no_citations_found":
                print(f"    [{pair.get('ref_number')}] {pair['status']}: {pair.get('error_message', '')}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Run the citation fidelity checker pipeline.")
    parser.add_argument(
        "--input", type=Path, default=Path("data/sample_input.json"),
        help="Path to a JSON file with 'reference_list' and 'sentences'.",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("output/report.json"),
        help="Where to write the JSON report.",
    )
    args = parser.parse_args()

    try:
        contact_email = require_contact_email()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        reference_list, sentences = load_input(args.input)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error loading input from {args.input}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Processing {len(sentences)} sentences against {len(reference_list)} references from {args.input}...")

    # reference_list values are the original entry dicts (number/doi, and for
    # identifier-free entries title/first_author/publication_year/raw), so the
    # pipeline can route DOI-less citations through extended retrieval and the
    # metadata module still sees the same entries it would from /api/analyze.
    reference_entries = [dict(entry) for entry in reference_list.values()]

    pair_results, metadata_report = run_content_and_metadata(
        sentences, reference_list, reference_entries, body_text="", contact_email=contact_email
    )
    report = generate_report(pair_results)
    report["bibliography_status"] = metadata_report["bibliography_status"]
    report["document_level_findings"] = metadata_report["document_level_findings"]
    report["not_evaluated_checks"] = metadata_report["not_evaluated_checks"]
    if "error" in metadata_report:
        report["metadata_error"] = metadata_report["error"]
        print(f"Warning: {metadata_report['error']}", file=sys.stderr)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"Report written to {args.output}")
    print_human_summary(report)


if __name__ == "__main__":
    main()
