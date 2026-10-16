from __future__ import annotations

import hashlib
from typing import Any

_JUDGE_PROMPT_VERSIONS = {
    "judge_v2.1": {
        "name": "judge_v2.1",
        "family": "judge",
        "version": "v2.1",
        "template": "offline judge prompt v2.1",
        "mode": "offline",
    },
    "judge_v2.2.2": {
        "name": "judge_v2.2.2",
        "family": "judge",
        "version": "v2.2.2",
        "template": "offline judge prompt v2.2.2",
        "mode": "offline",
    },
    "judge_v3.7": {
        "name": "judge_v3.7",
        "family": "judge",
        "version": "v3.7",
        "template": "offline judge prompt v3.7",
        "mode": "offline",
    },
}


def get_adapter_config() -> dict[str, Any]:
    active = "judge_v2.2.2"
    version_names = ["judge_v2.1", "judge_v2.2.2", "judge_v3.7"]
    return {
        "active": active,
        "judge_prompt_versions": [
            {"name": "judge_v2.1", "family": "judge", "version": "v2.1", "mode": "offline"},
            {"name": "judge_v2.2.2", "family": "judge", "version": "v2.2.2", "mode": "offline"},
            {"name": "judge_v3.7", "family": "judge", "version": "v3.7", "mode": "offline"},
        ],
        "supported_versions": version_names,
        "swap_policy": {
            "replay": "judge_v2.1",
            "live": "judge_v2.2.2",
            "definitive": "judge_v3.7",
        },
        "definitive_components": {
            "judge_adapter": "judge_3_v7",
            "winner_prompt_hash": hashlib.sha256(b"judge prompt v3.7 offline deterministic mode").hexdigest(),
            "cascade_config": "zablon_default_offline",
            "extended_retrieval_enabled": False,
        },
        "extended_retrieval_enabled": False,
    }


def _normalize_version_name(stage: str, version: str) -> str:
    key = version.strip().lower().replace(".", "")
    if stage == "judge":
        if key in {"v21", "2.1", "21"}:
            return "judge_v2.1"
        if key in {"v222", "2.2.2", "222"}:
            return "judge_v2.2.2"
        if key in {"v37", "3.7", "37"}:
            return "judge_v3.7"
        if key in {"judge_v21", "judge_v2.1"}:
            return "judge_v2.1"
        if key in {"judge_v222", "judge_v2.2.2"}:
            return "judge_v2.2.2"
        if key in {"judge_v37", "judge_v3.7", "judge_3_v7", "3_v7"}:
            return "judge_v3.7"
    return f"{stage}_{version.strip()}" if version.strip() else f"{stage}_default"


def _prompt_body(stage: str, version_name: str) -> str:
    if version_name == "judge_v2.1":
        return "judge prompt v2.1 offline deterministic mode"
    if version_name == "judge_v2.2.2":
        return "judge prompt v2.2.2 offline deterministic mode"
    if version_name == "judge_v3.7":
        return "judge prompt v3.7 offline deterministic mode"
    return f"{stage} prompt {version_name} offline deterministic mode"


def apply_prompt_swap(stage: str, version: str, *, prompt_root: str | None = None) -> dict[str, Any]:
    version_name = _normalize_version_name(stage, version)
    prompt_text = _prompt_body(stage, version_name)
    digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    return {
        "stage": stage,
        "selected_version": version_name,
        "version": version_name.rsplit("_", 1)[-1],
        "prompt_hash": digest,
        "prompt_text": prompt_text,
        "mode": "offline",
        "prompt_root": prompt_root,
    }


__all__ = ["apply_prompt_swap", "get_adapter_config"]
