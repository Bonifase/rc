"""
The Finding shape every check_NN function returns, per the spec exactly:

{
  "check_id": 7,
  "severity": "critical" | "warning",
  "reference_id": 11,
  "field": "journal",
  "declared": "Communications Biology",
  "actual": "Scientific Reports",
  "message": "..."
}

reference_id is Optional because a few checks (2, 4, 13's orphaned-marker
direction) are about the document as a whole or about a citation marker
that doesn't resolve to any reference at all -- there's no single
reference_id to attach in those cases.
"""
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Finding:
    check_id: int
    severity: str  # "critical" | "warning"
    message: str
    reference_id: Optional[int] = None
    field: Optional[str] = None
    declared: Optional[str] = None
    actual: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)
