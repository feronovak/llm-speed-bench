from __future__ import annotations

import copy
from typing import Any


SUPPORTED_PRESETS = {"json", "no-reasoning", "low-latency", "structured"}


def _setdefault_nested(
    target: dict[str, Any], path: tuple[str, ...], value: Any
) -> None:
    current = target
    for key in path[:-1]:
        current = current.setdefault(key, {})
    current.setdefault(path[-1], copy.deepcopy(value))


def expand_presets(request: dict[str, Any], presets: list[str]) -> dict[str, Any]:
    names = [str(preset) for preset in presets]
    unknown = sorted(set(names) - SUPPORTED_PRESETS)
    if unknown:
        raise ValueError(f"unknown presets: {', '.join(unknown)}")

    expanded: list[str] = []
    for preset in names:
        expanded.extend(
            ["json", "no-reasoning", "low-latency"]
            if preset == "structured"
            else [preset]
        )

    updated = copy.deepcopy(request)
    provider_options = updated.setdefault("provider_options", {})

    if "json" in expanded:
        for provider in ("openai", "openai_compatible", "xai", "openrouter"):
            _setdefault_nested(
                provider_options,
                (provider, "response_format"),
                {"type": "json_object"},
            )
        _setdefault_nested(
            provider_options,
            ("gemini", "generationConfig", "responseMimeType"),
            "application/json",
        )

    if "no-reasoning" in expanded:
        _setdefault_nested(provider_options, ("openrouter", "include_reasoning"), False)
        _setdefault_nested(
            provider_options,
            ("openrouter", "reasoning"),
            {"enabled": False},
        )
        _setdefault_nested(
            provider_options,
            ("gemini", "generationConfig", "thinkingConfig", "includeThoughts"),
            False,
        )

    if "low-latency" in expanded:
        updated.setdefault("temperature", 0)
        updated.setdefault("max_output_tokens", 256)

    return updated
