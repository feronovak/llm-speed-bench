from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_COMPARISON_FIELDS = (
    "name",
    "created",
    "context_length",
    "max_output_tokens",
    "capabilities",
    "input_cost_per_million",
    "output_cost_per_million",
    "pricing_metadata",
    "catalog_type",
    "catalog_confidence",
)

_CAPABILITY_SCHEMA_VERSION = 1
_CAPABILITY_MIGRATION_FIELDS = {
    "capabilities",
    "catalog_type",
    "catalog_confidence",
}


def snapshot_catalog(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep stable, non-secret catalog fields for local comparison."""
    return [
        {
            "capability_schema_version": _CAPABILITY_SCHEMA_VERSION,
            **{
                key: copy.deepcopy(model[key])
                for key in ("provider", "model", *_COMPARISON_FIELDS)
                if key in model
            },
        }
        for model in models
    ]


def _by_identity(models: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (model.get("provider", "openai_compatible"), model["model"]): model
        for model in models
    }


def catalog_diff(
    previous: list[dict[str, Any]], current: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    before = _by_identity(previous)
    after = _by_identity(current)
    added_keys = sorted(after.keys() - before.keys())
    removed_keys = sorted(before.keys() - after.keys())
    renamed = []
    changed = []
    for key in sorted(before.keys() & after.keys()):
        old = before[key]
        new = after[key]
        legacy_capability_metadata = (
            old.get("capability_schema_version") != _CAPABILITY_SCHEMA_VERSION
        )
        if old.get("name") != new.get("name"):
            renamed.append(
                {
                    "provider": key[0],
                    "model": key[1],
                    "before": old.get("name", key[1]),
                    "after": new.get("name", key[1]),
                }
            )
        fields = [
            field
            for field in _COMPARISON_FIELDS
            if field != "name"
            and old.get(field) != new.get(field)
            and not (
                field in _CAPABILITY_MIGRATION_FIELDS and legacy_capability_metadata
            )
        ]
        if fields:
            changed.append({"provider": key[0], "model": key[1], "fields": fields})
    return {
        "added": [after[key] for key in added_keys],
        "removed": [before[key] for key in removed_keys],
        "renamed": renamed,
        "changed": changed,
    }


def build_candidate_config(
    watch_config: dict[str, Any],
    approved_config: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Use watch test settings while comparing new candidates with incumbents."""
    config = copy.deepcopy(watch_config)
    approved = copy.deepcopy(approved_config.get("models", []))
    seen = {
        (model.get("provider", "openai_compatible"), model["model"])
        for model in approved
    }
    models = approved
    for candidate in candidates:
        key = (candidate.get("provider", "openai_compatible"), candidate["model"])
        if key not in seen:
            models.append(copy.deepcopy(candidate))
            seen.add(key)
    config["models"] = models
    config["discovery"] = []
    return config


def default_snapshot_path(config_path: Path) -> Path:
    return config_path.parent / ".llm-bench" / "catalogs" / f"{config_path.stem}.json"


def load_snapshot(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["models"] if isinstance(payload, dict) else payload


def save_snapshot(path: Path, models: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "models": snapshot_catalog(models),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
