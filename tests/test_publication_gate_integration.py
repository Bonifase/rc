import copy
import hashlib
import json

from orchestrator.runner import _apply_publication_gate


def test_apply_publication_gate_blocks_manifest_hash_mismatch():
    content = {
        "kind": "content",
        "reference_id": 5,
        "marker": "[5]",
        "source_label": "Example Source",
        "error_type": "source_says_opposite",
        "claim": "The paper finds a strong effect.",
        "evidence": [
            {"text": "The study found no effect in the observed cohort."},
            {"text": "The effect was not significant."},
        ],
        "observation": "The source does not report the claimed effect.",
        "reference": "https://doi.org/10.1/example",
        "passage_origin": "full_text",
        "count_as_reference": True,
    }
    metadata = {
        "kind": "metadata",
        "reference_id": 6,
        "marker": "[6]",
        "source_label": "Smith (2020)",
        "error_type": "wrong_reference_details",
        "claim": "Statement citing [6].",
        "reference_as_generated": "Smith (2020). An inaccurate citation.",
        "reference_official": "Jones, J. (2021). Correct source. Journal X. https://doi.org/10.1/jones",
        "findings": ["The correct title is Correct source, not An inaccurate citation."],
        "count_as_reference": True,
    }
    report = {
        "lang": "en",
        "report_complete": True,
        "cases": [content, metadata],
        "references_reviewed": 2,
    }
    manifest = {
        "bibliography": [
            {"reference_id": 5, "marker": "[5]", "reference": "Source citation 5"},
            {"reference_id": 6, "marker": "[6]", "reference": "Smith (2020). An inaccurate citation."},
        ],
        "citations": [
            {"reference_id": 5, "marker": "[5]", "claim": "The paper finds a strong effect."},
            {"reference_id": 6, "marker": "[6]", "claim": "Statement citing [6]."},
        ],
        "orphan_citations": [],
        "references_reviewed": 2,
    }
    official = {
        "6": {
            "verified_title_match": True,
            "verified_first_author_match": True,
            "official_ref_text": "Jones, J. (2021). Correct source. Journal X. https://doi.org/10.1/jones",
            "actual_fields": {
                "doi": "10.1/jones",
                "title": "Correct source",
                "authors": [{"family": "Jones", "given": "J"}],
                "year": 2021,
                "journal": "Journal X",
                "volume": None,
                "issue": None,
                "pages": None,
            },
        }
    }

    result = _apply_publication_gate(report, manifest, official, "0" * 64)

    assert result["report_complete"] is False
    assert result["publication_status"] == "draft"
    assert result["publication_errors"]


def test_apply_publication_gate_accepts_valid_render_data():
    content = {
        "kind": "content",
        "reference_id": 5,
        "marker": "[5]",
        "source_label": "Example Source",
        "error_type": "source_says_opposite",
        "claim": "The paper finds a strong effect.",
        "evidence": [
            {"text": "The study found no effect in the observed cohort."},
            {"text": "The effect was not significant."},
        ],
        "observation": "The source does not report the claimed effect.",
        "reference": "https://doi.org/10.1/example",
        "passage_origin": "full_text",
        "count_as_reference": True,
    }
    metadata = {
        "kind": "metadata",
        "reference_id": 6,
        "marker": "[6]",
        "source_label": "Smith (2020)",
        "error_type": "wrong_reference_details",
        "claim": "Statement citing [6].",
        "reference_as_generated": "Smith (2020). An inaccurate citation.",
        "reference_official": "**Jones, J. (2021). Correct source. Journal X. https://doi.org/10.1/jones**",
        "findings": ["The correct title is Correct source, not An inaccurate citation."],
        "count_as_reference": True,
    }
    report = {
        "lang": "en",
        "report_complete": True,
        "cases": [content, metadata],
        "references_reviewed": 2,
    }
    manifest = {
        "bibliography": [
            {"reference_id": 5, "marker": "[5]", "reference": "Source citation 5"},
            {"reference_id": 6, "marker": "[6]", "reference": "Smith (2020). An inaccurate citation."},
        ],
        "citations": [
            {"reference_id": 5, "marker": "[5]", "claim": "The paper finds a strong effect."},
            {"reference_id": 6, "marker": "[6]", "claim": "Statement citing [6]."},
        ],
        "orphan_citations": [],
        "references_reviewed": 2,
    }
    official = {
        "6": {
            "verified_title_match": True,
            "verified_first_author_match": True,
            "official_ref_text": "Jones, J. (2021). Correct source. Journal X. https://doi.org/10.1/jones",
            "actual_fields": {
                "doi": "10.1/jones",
                "title": "Correct source",
                "authors": [{"family": "Jones", "given": "J"}],
                "year": 2021,
                "journal": "Journal X",
                "volume": None,
                "issue": None,
                "pages": None,
            },
        }
    }
    fixed_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf8")
    ).hexdigest()

    result = _apply_publication_gate(report, manifest, official, fixed_hash)

    assert result["report_complete"] is True
    assert result["publication_status"] == "final"
    assert not result["publication_errors"]
