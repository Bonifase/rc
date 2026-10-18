from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.acceptance_harness import orchestrator_accept

from .runner import run_orchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description="RefCheck CLI")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run the manuscript pipeline on a source document.")
    run_parser.add_argument("source", type=Path, help="Path to a .docx or .pdf manuscript.")
    run_parser.add_argument("--out-dir", type=Path, default=Path("output/orchestrator"), help="Directory for JSON/HTML/Excel outputs.")

    accept_parser = subparsers.add_parser("accept", help="Run the offline acceptance harness for the judge integration.")
    accept_parser.add_argument("--mode", choices=["replay", "live"], default="replay", help="Offline replay or live-mode alias for the acceptance check.")
    accept_parser.add_argument("--manuscript", type=Path, default=None, help="Optional manuscript path for the acceptance harness.")

    estimate_parser = subparsers.add_parser("estimate", help="Parse a manuscript and count the model calls a live run could make. Free.")
    estimate_parser.add_argument("source", type=Path, help="Path to a .docx or .pdf manuscript.")

    args = parser.parse_args()

    if args.command == "estimate":
        from .estimate import estimate_run

        print(json.dumps(estimate_run(args.source), ensure_ascii=False, indent=2))
        return

    if args.command == "accept":
        result = orchestrator_accept(mode=args.mode, manuscript_path=str(args.manuscript) if args.manuscript else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command in {None, "run"}:
        source = args.source if hasattr(args, "source") else None
        if source is None:
            parser.error("A manuscript path is required unless you use the 'accept' subcommand.")
        result = run_orchestrator(source, getattr(args, "out_dir", Path("output/orchestrator")))
        print(result["status"])
        print(result["report_json"])
        print(result["html_report"])
        print(result["excel_dataset"])
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
