from __future__ import annotations

from pathlib import Path
from typing import Any

from .capability_ledger import record_probe
from .client import create_client


def probe_model(
    model: dict[str, Any], ledger_path: Path, timeout: float = 30
) -> dict[str, Any]:
    """Make one minimal provider-native request and persist only safe evidence."""
    result = create_client(model, timeout).run(
        "Reply with OK.",
        {"max_output_tokens": 32, "retry": {"max_attempts": 1}},
    )
    if result["ok"]:
        outcome = "text-ready"
        error_category = None
    else:
        raw_category = result.get("failure_category")
        error_category = str(raw_category) if raw_category else None
        if error_category == "not_found":
            outcome = "unavailable"
        elif error_category == "unsupported_parameter":
            outcome = "text-special"
        else:
            outcome = "indeterminate"
    probe = {
        "provider": model.get("provider", "openai_compatible"),
        "model": model["model"],
        "adapter": (model.get("capabilities") or {}).get("adapter"),
        "outcome": outcome,
        "fingerprint": (model.get("capabilities") or {}).get("fingerprint"),
        "request_options": {"max_output_tokens": 32},
        "error_category": error_category,
    }
    record_probe(ledger_path, probe)
    return probe
