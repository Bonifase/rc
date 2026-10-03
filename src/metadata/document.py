"""
The `doc` argument every check_NN(doc, references, crossref_records)
function receives. Keeps the document-level context (sentences, raw body
text) separate from the reference list, matching the spec's exact function
signature while giving Phase A checks (2, 3, 4) something to scan for
declared counts and repeated figures.
"""
from dataclasses import dataclass, field


@dataclass
class MetadataDoc:
    sentences: list[dict]  # [{"id": "s1", "text": "..."}, ...]
    body_text: str = ""  # full raw body text, for checks that scan prose (2, 3, 4)
