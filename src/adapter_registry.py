from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_PROMPT_ROOT = _ROOT / "judge_prompts"

_JUDGE_PROMPT_VERSIONS = {
    "judge_v2.1": {
        "name": "judge_v2.1",
        "family": "judge",
        "version": "v2.1",
        "template": "judge_hybrid_sole_v2_1.txt / judge_hybrid_multicita_v2_1.txt",
        "mode": "offline",
    },
    "judge_v2.2.2": {
        "name": "judge_v2.2.2",
        "family": "judge",
        "version": "v2.2.2",
        "template": "judge_hybrid_sole_v2_2_2.txt / judge_hybrid_multicita_v2_2_2.txt",
        "mode": "offline",
    },
}


def _normalize_version_name(stage: str, version: str) -> str:
    key = (version or "").strip().lower().replace(".", "").replace("_", "")
    if stage == "judge":
        if key in {"v21", "21", "judgev21"}:
            return "judge_v2.1"
        if key in {"v222", "222", "judgev222"}:
            return "judge_v2.2.2"
        if key in {"judge_v21", "judge_v2.1"}:
            return "judge_v2.1"
        if key in {"judge_v222", "judge_v2.2.2", "judge_hybrid_v2_2_2"}:
            return "judge_v2.2.2"
    return f"{stage}_{version.strip()}" if version and version.strip() else f"{stage}_default"


def _prompt_filename(version_name: str, citation_context: str = "sole") -> str:
    context = "sole" if str(citation_context).strip().lower() == "sole" else "multicita"
    suffix_map = {
        "judge_v2.1": "v2_1",
        "judge_v2.2.2": "v2_2_2",
    }
    suffix = suffix_map.get(version_name, "v2_2_2")
    return f"judge_hybrid_{context}_{suffix}.txt"


def _resolve_prompt_path(version_name: str, *, prompt_root: str | Path | None = None, citation_context: str = "sole") -> Path:
    base = Path(prompt_root) if prompt_root is not None else _PROMPT_ROOT
    return base / _prompt_filename(version_name, citation_context)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prompt_body(stage: str, version_name: str) -> str:
    if version_name == "judge_v2.1":
        return "judge hybrid prompt v2.1 offline deterministic mode"
    if version_name == "judge_v2.2.2":
        return "judge hybrid prompt v2.2.2 offline deterministic mode"
    return f"{stage} prompt {version_name} offline deterministic mode"


def get_adapter_config() -> dict[str, Any]:
    active = "judge_v2.2.2"
    version_names = ["judge_v2.1", "judge_v2.2.2"]
    sole_hash = _sha256_file(_resolve_prompt_path("judge_v2.2.2", citation_context="sole"))
    multicita_hash = _sha256_file(_resolve_prompt_path("judge_v2.2.2", citation_context="multicita"))
    return {
        "active": active,
        "judge_prompt_versions": [
            {"name": "judge_v2.1", "family": "judge", "version": "v2.1", "mode": "offline"},
            {"name": "judge_v2.2.2", "family": "judge", "version": "v2.2.2", "mode": "offline"},
        ],
        "supported_versions": version_names,
        "swap_policy": {
            "replay": "judge_v2.2.2",
            "live": "judge_v2.2.2",
            "definitive": "judge_v2.2.2",
        },
        "definitive_components": {
            "judge_adapter": "judge_hybrid_v2_2_2",
            "winner_prompt_hash": sole_hash,
            "supporting_prompt_hashes": {
                "sole": sole_hash,
                "multicita": multicita_hash,
            },
            "cascade_config": "offline_luna_sol_6x6",
            "extended_retrieval_enabled": False,
        },
        "extended_retrieval_enabled": False,
    }


def apply_prompt_swap(stage: str, version: str, *, prompt_root: str | None = None, citation_context: str = "sole") -> dict[str, Any]:
    version_name = _normalize_version_name(stage, version)
    prompt_path = _resolve_prompt_path(version_name, prompt_root=prompt_root, citation_context=citation_context)
    prompt_text = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else _prompt_body(stage, version_name)
    digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    if prompt_path.exists():
        digest = _sha256_file(prompt_path)
    return {
        "stage": stage,
        "selected_version": version_name,
        "version": version_name.rsplit("_", 1)[-1],
        "prompt_hash": digest,
        "prompt_text": prompt_text,
        "mode": "offline",
        "prompt_root": str(prompt_path.parent),
        "citation_context": "sole" if str(citation_context).strip().lower() == "sole" else "multicita",
    }


__all__ = ["apply_prompt_swap", "get_adapter_config"]
