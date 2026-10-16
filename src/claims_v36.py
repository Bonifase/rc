from __future__ import annotations

import re
from typing import Any


def _split_claims(claim_text: str) -> list[str]:
    text = (claim_text or "").strip()
    if not text:
        return []
    parts = re.split(r"\s+(?:and|while|however|therefore|because|so)\s+", text)
    if len(parts) == 1:
        parts = re.split(r"(?<=[.;:])\s+", text)
    cleaned = []
    for part in parts:
        sub = part.strip()
        if sub:
            cleaned.append(sub)
    return cleaned or [text]


def claims_v36(claim_text: str, *, decomposition: str = "01b") -> list[dict[str, Any]]:
    """Return a list of claim atoms for the v3.6 decomposition contract.

    The 01B decomposition splits long claims into atomic sub-claims while keeping
    the original meaning. This path is intentionally deterministic and offline-safe.
    """
    items: list[dict[str, Any]] = []
    for index, part in enumerate(_split_claims(claim_text), start=1):
        cleaned = part.strip().rstrip(".")
        if not cleaned:
            continue
        items.append(
            {
                "id": f"claim_v36_{index}",
                "text": cleaned,
                "decomposition": decomposition or "01b",
                "coverage": "v3.6",
            }
        )
    return items or [{"id": "claim_v36_1", "text": (claim_text or "").strip(), "decomposition": decomposition or "01b", "coverage": "v3.6"}]


__all__ = ["claims_v36"]
