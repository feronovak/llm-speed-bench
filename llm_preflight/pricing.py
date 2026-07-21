from __future__ import annotations

from datetime import date
from typing import Any


# Standard synchronous API rates in USD per million tokens. Provider catalogs
# do not consistently return prices, so these public rates fill that gap.
# OpenRouter prices remain dynamic and take precedence when its catalog returns
# them. Gemini 3.1 Pro uses the <=200k-input tier.
PUBLIC_PRICING: dict[tuple[str, str], tuple[float, float, str]] = {
    ("openai", "gpt-5.6-luna"): (1.0, 6.0, "2026-07-14"),
    ("openai", "gpt-5.6-terra"): (2.5, 15.0, "2026-07-14"),
    ("openai", "gpt-5.6-sol"): (5.0, 30.0, "2026-07-14"),
    ("openai", "gpt-5.5"): (5.0, 30.0, "2026-07-09"),
    ("openai", "gpt-5.4-mini"): (0.75, 4.5, "2026-07-09"),
    ("openai", "gpt-5.4-nano"): (0.2, 1.25, "2026-07-09"),
    ("openai", "gpt-4.1"): (2.0, 8.0, "2026-07-09"),
    ("openai", "gpt-4.1-mini"): (0.4, 1.6, "2026-07-09"),
    ("openai", "gpt-4.1-nano"): (0.1, 0.4, "2026-07-09"),
    ("gemini", "gemini-3.1-pro-preview"): (2.0, 12.0, "2026-07-09"),
    ("gemini", "gemini-3.5-flash"): (1.5, 9.0, "2026-07-09"),
    # Introductory Sonnet 5 rate through 2026-08-31.
    ("anthropic", "claude-sonnet-5"): (2.0, 10.0, "2026-07-09"),
    ("anthropic", "claude-fable-5"): (10.0, 50.0, "2026-07-09"),
    ("anthropic", "claude-opus-4-8"): (5.0, 25.0, "2026-07-09"),
    ("xai", "grok-4.3"): (1.25, 2.5, "2026-07-09"),
}

PUBLIC_PRICING_DETAILS: dict[tuple[str, str], dict[str, Any]] = {
    ("gemini", "gemini-3.1-pro-preview"): {
        "cached_input_cost_per_million": 0.2,
        "pricing_tiers": [
            {
                "up_to_input_tokens": 200_000,
                "input_cost_per_million": 2.0,
                "output_cost_per_million": 12.0,
                "cached_input_cost_per_million": 0.2,
            },
            {
                "input_cost_per_million": 4.0,
                "output_cost_per_million": 18.0,
                "cached_input_cost_per_million": 0.4,
            },
        ],
    }
}


def _pricing_tier(model: dict[str, Any], input_tokens: int) -> dict[str, Any]:
    tiers = model.get("pricing_tiers")
    if isinstance(tiers, list):
        for tier in tiers:
            if not isinstance(tier, dict):
                continue
            maximum = tier.get("up_to_input_tokens")
            if maximum is None or input_tokens <= int(maximum):
                return {**model, **tier}
    return model


def estimate_sample_cost(sample: dict[str, Any], model: dict[str, Any]) -> float | None:
    """Estimate one request using its cache hits and applicable input tier."""
    input_tokens = sample.get("input_tokens")
    output_tokens = sample.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        return None
    tier = _pricing_tier(model, int(input_tokens))
    input_price = tier.get("input_cost_per_million")
    output_price = tier.get("output_cost_per_million")
    if input_price is None or output_price is None:
        return None
    cached_input = min(
        max(0, int(sample.get("cached_input_tokens") or 0)), int(input_tokens)
    )
    cached_price = tier.get("cached_input_cost_per_million", input_price)
    return (
        (int(input_tokens) - cached_input) * float(input_price) / 1_000_000
        + cached_input * float(cached_price) / 1_000_000
        + int(output_tokens) * float(output_price) / 1_000_000
    )


def apply_public_pricing(model: dict[str, Any]) -> dict[str, Any]:
    if (
        model.get("input_cost_per_million") is not None
        and model.get("output_cost_per_million") is not None
    ):
        if model.get("pricing_metadata"):
            return model
        return {**model, "pricing_metadata": {"source": "user override"}}
    key = (model.get("provider", "openai_compatible"), model["model"])
    pricing = PUBLIC_PRICING.get(key)
    if pricing is None:
        return model
    input_price, output_price, as_of = pricing
    return {
        **model,
        "input_cost_per_million": input_price,
        "output_cost_per_million": output_price,
        **PUBLIC_PRICING_DETAILS.get(key, {}),
        "pricing_metadata": {
            "source": "official snapshot",
            "confidence": "official",
            "as_of": as_of,
        },
    }


def pricing_freshness_report(
    models: list[dict[str, Any]],
    today: date | None = None,
    max_age_days: int = 30,
) -> dict[str, Any]:
    current = today or date.today()
    warnings = []
    for model in models:
        provider = model.get("provider", "openai_compatible")
        name = model["model"]
        metadata = model.get("pricing_metadata") or {}
        source = metadata.get("source")
        if (
            model.get("input_cost_per_million") is None
            or model.get("output_cost_per_million") is None
        ):
            warnings.append(
                {
                    "model": name,
                    "provider": provider,
                    "severity": "warning",
                    "message": "pricing is unknown",
                    "source": "unknown",
                    "as_of": None,
                }
            )
            continue
        as_of = metadata.get("as_of")
        if source == "official snapshot" and as_of:
            age_days = (current - date.fromisoformat(as_of)).days
            if age_days > max_age_days:
                warnings.append(
                    {
                        "model": name,
                        "provider": provider,
                        "severity": "warning",
                        "message": (
                            f"official pricing snapshot is stale by {age_days} days"
                        ),
                        "source": source,
                        "as_of": as_of,
                    }
                )
    return {"ok": not warnings, "warnings": warnings}
