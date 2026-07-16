from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "probes": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"invalid capability ledger: {path}")
    if not isinstance(payload.get("probes"), dict):
        raise ValueError(f"invalid capability ledger probes: {path}")
    return payload


def record_probe(path: Path, probe: dict[str, Any]) -> None:
    provider = str(probe.get("provider", ""))
    model = str(probe.get("model", ""))
    if not provider or not model:
        raise ValueError("probe requires provider and model")
    allowed = {
        "adapter",
        "outcome",
        "fingerprint",
        "request_options",
        "error_category",
        "observed_at",
    }
    entry = {key: value for key, value in probe.items() if key in allowed}
    entry["observed_at"] = (
        entry.get("observed_at") or datetime.now(timezone.utc).isoformat()
    )
    ledger = load_ledger(path)
    ledger["probes"][f"{provider}:{model}"] = entry
    _write_private_json(path, ledger)


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def apply_probe_evidence(
    models: list[dict[str, Any]], ledger: dict[str, Any]
) -> list[dict[str, Any]]:
    """Apply only matching local probe evidence; a changed fingerprint expires it."""
    probes = ledger.get("probes", {})
    enriched = []
    for model in models:
        copy = dict(model)
        key = f"{copy.get('provider', 'openai_compatible')}:{copy['model']}"
        probe = probes.get(key)
        fingerprint = (copy.get("capabilities") or {}).get("fingerprint")
        if probe and (
            not probe.get("fingerprint") or probe.get("fingerprint") == fingerprint
        ):
            copy["catalog_type"] = probe["outcome"]
            copy["catalog_confidence"] = "probe"
            copy["capabilities"] = {
                **(copy.get("capabilities") or {}),
                **({"adapter": probe["adapter"]} if probe.get("adapter") else {}),
            }
            copy["capability_evidence"] = [
                *(copy.get("capability_evidence") or []),
                {
                    "source": "probe",
                    "confidence": "high",
                    "observed_at": probe["observed_at"],
                },
            ]
        enriched.append(copy)
    return enriched
