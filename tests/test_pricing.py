from datetime import date

import pytest

from llm_preflight.pricing import apply_public_pricing, pricing_freshness_report


def test_public_pricing_marks_user_overrides():
    model = apply_public_pricing(
        {
            "provider": "openai",
            "model": "gpt-4.1",
            "input_cost_per_million": 99,
            "output_cost_per_million": 100,
        }
    )

    assert model["pricing_metadata"] == {"source": "user override"}


def test_public_pricing_marks_official_snapshot():
    model = apply_public_pricing({"provider": "openai", "model": "gpt-5.4-mini"})

    assert model["pricing_metadata"]["source"] == "official snapshot"
    assert model["pricing_metadata"]["confidence"] == "official"


def test_gemini_3_1_pricing_has_cache_and_long_context_tiers():
    model = apply_public_pricing(
        {"provider": "gemini", "model": "gemini-3.1-pro-preview"}
    )

    assert model["cached_input_cost_per_million"] == 0.2
    assert model["pricing_tiers"][1] == {
        "input_cost_per_million": 4.0,
        "output_cost_per_million": 18.0,
        "cached_input_cost_per_million": 0.4,
    }


@pytest.mark.parametrize(
    ("model_id", "input_price", "output_price"),
    [
        ("gpt-5.6-luna", 1.0, 6.0),
        ("gpt-5.6-terra", 2.5, 15.0),
        ("gpt-5.6-sol", 5.0, 30.0),
    ],
)
def test_gpt_5_6_official_snapshot_pricing(model_id, input_price, output_price):
    model = apply_public_pricing({"provider": "openai", "model": model_id})

    assert model["input_cost_per_million"] == input_price
    assert model["output_cost_per_million"] == output_price


def test_pricing_freshness_report_flags_stale_public_registry_entries():
    report = pricing_freshness_report(
        [
            {
                "provider": "openai",
                "model": "gpt-4.1",
                "input_cost_per_million": 2,
                "output_cost_per_million": 8,
                "pricing_metadata": {
                    "source": "official snapshot",
                    "as_of": "2026-01-01",
                },
            }
        ],
        today=date(2026, 7, 13),
        max_age_days=30,
    )

    assert report["ok"] is False
    assert report["warnings"] == [
        {
            "model": "gpt-4.1",
            "provider": "openai",
            "severity": "warning",
            "message": "official pricing snapshot is stale by 193 days",
            "source": "official snapshot",
            "as_of": "2026-01-01",
        }
    ]


def test_pricing_freshness_report_flags_unknown_prices():
    report = pricing_freshness_report(
        [{"provider": "openai_compatible", "model": "local"}],
        today=date(2026, 7, 13),
    )

    assert report["ok"] is False
    assert report["warnings"][0]["message"] == "pricing is unknown"
