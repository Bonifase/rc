"""Where RefCheck keeps files that must outlive a request or a restart.

Set REFCHECK_DATA_DIR to a persistent disk in production; the default is ./output.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    return Path(os.getenv("REFCHECK_DATA_DIR") or ROOT / "output")


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temporary file so a kill mid-write never leaves a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
