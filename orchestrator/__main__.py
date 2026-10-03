from __future__ import annotations

import argparse
from pathlib import Path

from .runner import run_orchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RefCheck orchestrator on a manuscript file.")
    parser.add_argument("source", type=Path, help="Path to a .docx or .pdf manuscript.")
    parser.add_argument("--out-dir", type=Path, default=Path("output/orchestrator"), help="Directory for JSON/HTML/Excel outputs.")
    args = parser.parse_args()

    result = run_orchestrator(args.source, args.out_dir)
    print(result["status"])
    print(result["report_json"])
    print(result["html_report"])
    print(result["excel_dataset"])


if __name__ == "__main__":
    main()
