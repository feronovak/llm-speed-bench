import pytest

from llm_bench.features import (
    apply_environment,
    apply_model_aliases,
    apply_provider_presets,
    apply_smoke_mode,
    check_budget,
    compare_results,
    doctor_report,
    estimate_budget,
    filter_changed_models,
    matrix_report,
    replay_config,
)


def _summary(latency, cost, success=1.0):
    return {
        "requests": 2,
        "successful": round(2 * success),
        "failed": 2 - round(2 * success),
        "success_rate": success,
        "latency_seconds": {"mean": latency, "p50": latency, "p95": latency},
        "ttft_seconds": {"p50": latency / 2 if latency is not None else None},
        "output_tokens_per_second": {"p50": 10},
        "input_tokens": 10,
        "output_tokens": 5,
        "estimated_cost_usd": cost,
    }


def test_smoke_mode_forces_one_measured_request_without_warmup():
    config = apply_smoke_mode(
        {"prompt": "hi", "repetitions": 9, "warmups": 3, "suite_repetitions": 4}
    )
    assert config["repetitions"] == 1
    assert config["suite_repetitions"] == 1
    assert config["warmups"] == 0
    assert config["name"].endswith("-smoke")


def test_doctor_report_flags_missing_keys_and_model_count(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    report = doctor_report(
        {
            "prompt": "hi",
            "models": [{"provider": "openai", "model": "gpt-test"}],
        }
    )
    assert report["ok"] is False
    assert report["models"] == 1
    assert "OPENAI_API_KEY" in report["checks"][0]["message"]


def test_compare_results_reports_metric_deltas_and_regressions():
    baseline = {
        "models": [{"name": "fast", "summary": _summary(1.0, 0.01, 1.0)}],
    }
    current = {
        "models": [{"name": "fast", "summary": _summary(1.5, 0.02, 0.5)}],
    }
    diff = compare_results(baseline, current)
    row = diff["models"][0]
    assert row["name"] == "fast"
    assert row["latency_p95_delta_seconds"] == 0.5
    assert row["cost_delta_usd"] == 0.01
    assert row["success_rate_delta"] == -0.5
    assert "success_rate" in row["regressions"]


def test_replay_config_uses_saved_source_config_and_exact_model_set():
    result = {
        "source_config": {
            "prompt": "hi",
            "models": [{"provider": "openai", "model": "old"}],
            "discovery": [{"provider": "openai", "limit": 5}],
        },
        "models": [{"provider": "openai", "model": "old"}],
        "settings": {"repetitions": 2, "warmups": 0, "concurrency": 1},
    }
    config = replay_config(result)
    assert config["prompt"] == "hi"
    assert config["models"] == [{"provider": "openai", "model": "old"}]
    assert config["discovery"] == []
    assert config["repetitions"] == 2


def test_replay_config_preserves_model_endpoint_and_auth_metadata():
    result = {
        "source_config": {
            "prompt": "hi",
            "models": [{"provider": "openai_compatible", "model": "old"}],
        },
        "models": [
            {
                "provider": "openai_compatible",
                "model": "old",
                "name": "proxy-old",
                "base_url": "https://proxy.example.test/v1",
                "api_key_env": "PROXY_API_KEY",
                "api_version": "2026-01-01",
            }
        ],
        "settings": {"repetitions": 1, "warmups": 0},
    }

    config = replay_config(result)

    assert config["models"] == [
        {
            "provider": "openai_compatible",
            "model": "old",
            "name": "proxy-old",
            "base_url": "https://proxy.example.test/v1",
            "api_key_env": "PROXY_API_KEY",
            "api_version": "2026-01-01",
        }
    ]


def test_budget_check_rejects_excess_requests_and_cost():
    config = {
        "prompt": "hi",
        "models": [
            {
                "model": "expensive",
                "input_cost_per_million": 10,
                "output_cost_per_million": 20,
            }
        ],
        "repetitions": 3,
        "warmups": 1,
        "request": {"max_output_tokens": 1000},
        "max_requests": 2,
        "max_estimated_cost_usd": 0.0001,
    }
    with pytest.raises(ValueError, match="max_requests"):
        check_budget(config)
    config["max_requests"] = 10
    with pytest.raises(ValueError, match="max_estimated_cost_usd"):
        check_budget(config)


def test_budget_check_counts_mixed_builtin_and_custom_tests():
    config = {
        "models": [{"model": "fake"}],
        "profiles": "classification,csv-review",
        "suite_repetitions": 1,
        "max_requests": 3,
        "prompts": [
            {
                "name": "csv-review",
                "prompt": "Review CSV",
                "validation": {"contains": "ok"},
            }
        ],
    }

    with pytest.raises(ValueError, match="max_requests"):
        check_budget(config)


def test_estimated_budget_counts_warmups_per_profile():
    budget = estimate_budget(
        {
            "prompt": "hi",
            "models": [{"model": "fake"}],
            "profiles": "classification,reasoning",
            "suite_repetitions": 1,
            "warmups": 1,
        }
    )

    assert budget["requests"] == 8


def test_aliases_and_environment_overlays_are_applied_to_configs():
    config = {
        "prompt": "hi",
        "aliases": {"fast": {"provider": "openai", "model": "gpt-fast"}},
        "models": ["fast"],
        "environments": {"ci": {"repetitions": 1, "profiles": "classification"}},
    }
    config = apply_environment(config, "ci")
    config = apply_model_aliases(config)
    assert config["repetitions"] == 1
    assert config["profiles"] == "classification"
    assert config["models"] == [{"provider": "openai", "model": "gpt-fast"}]


def test_provider_presets_expand_to_provider_specific_request_options():
    config = apply_provider_presets(
        {
            "prompt": "Return JSON",
            "models": [{"provider": "gemini", "model": "gemini-test"}],
            "presets": ["structured"],
        }
    )

    request = config["request"]
    assert request["temperature"] == 0
    assert request["max_output_tokens"] == 256
    assert request["provider_options"]["openai"]["response_format"] == {
        "type": "json_object"
    }
    assert request["provider_options"]["openrouter"]["response_format"] == {
        "type": "json_object"
    }
    assert request["provider_options"]["openrouter"]["include_reasoning"] is False
    assert request["provider_options"]["openrouter"]["reasoning"] == {"enabled": False}
    assert request["provider_options"]["gemini"]["generationConfig"] == {
        "responseMimeType": "application/json",
        "thinkingConfig": {"includeThoughts": False},
    }


def test_provider_presets_do_not_override_explicit_request_options():
    config = apply_provider_presets(
        {
            "prompt": "Return JSON",
            "models": [{"provider": "openai", "model": "gpt-test"}],
            "presets": ["json", "low-latency"],
            "request": {
                "temperature": 0.7,
                "max_output_tokens": 64,
                "provider_options": {
                    "openai": {"response_format": {"type": "json_schema"}}
                },
            },
        }
    )

    assert config["request"]["temperature"] == 0.7
    assert config["request"]["max_output_tokens"] == 64
    assert config["request"]["provider_options"]["openai"]["response_format"] == {
        "type": "json_schema"
    }


def test_changed_model_filter_uses_previous_catalog_snapshot():
    models = [
        {"provider": "openai", "model": "old"},
        {"provider": "openai", "model": "new"},
    ]
    previous = [{"provider": "openai", "model": "old"}]
    assert filter_changed_models(models, previous) == [
        {"provider": "openai", "model": "new"}
    ]


def test_matrix_report_shows_model_by_profile_quality():
    result = {
        "models": [
            {
                "name": "model-a",
                "profiles": [
                    {"name": "classification", "summary": {"valid_output_rate": 1}},
                    {"name": "reasoning", "summary": {"valid_output_rate": 0.5}},
                ],
            }
        ]
    }
    rendered = matrix_report(result)
    assert "| Model | classification | reasoning |" in rendered
    assert "| model-a | 100% | 50% |" in rendered
