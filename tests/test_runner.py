import json
import stat
import sys

import pytest

from llm_preflight.runner import (
    _failed_tests,
    _profile_progress_callback,
    benchmark_run_lock,
    console_report,
    custom_prompt_profile,
    load_config,
    report,
    run_benchmark,
    save_result,
    select_custom_prompt,
    select_test_profiles,
    result_failed,
    validate_config_validations,
)


def test_benchmark_run_lock_reports_existing_run(tmp_path, monkeypatch):
    class BusyFcntl:
        LOCK_EX = 1
        LOCK_NB = 2

        @staticmethod
        def flock(_descriptor, _flags):
            raise BlockingIOError

    monkeypatch.setitem(sys.modules, "fcntl", BusyFcntl)

    with pytest.raises(ValueError, match="already running"):
        with benchmark_run_lock(tmp_path / "results"):
            pass


def test_result_with_a_model_that_produced_no_samples_fails_closed():
    assert result_failed({"models": [{"summary": {"requests": 0}, "samples": []}]})


def test_benchmark_records_the_source_config_path_without_putting_it_in_config():
    result = run_benchmark(
        {
            "prompt": "Reply with ok.",
            "models": [{"provider": "mock", "model": "local", "response": "ok"}],
            "warmups": 0,
            "repetitions": 1,
            "_source_config_path": "/workspace/benchmark.json",
        }
    )

    assert result["source_config_path"] == "/workspace/benchmark.json"
    assert "_source_config_path" not in result["source_config"]


def test_load_config_accepts_named_prompts_without_legacy_prompt(tmp_path):
    path = tmp_path / "benchmark.json"
    path.write_text(
        '{"prompts":[{"name":"csv-review","prompt":"Review this CSV"}],'
        '"models":[{"model":"fake"}]}'
    )

    assert load_config(path)["prompts"][0]["name"] == "csv-review"


def test_config_rejects_empty_contains_and_builtin_prompt_name_collisions():
    with pytest.raises(ValueError, match="contains must be a non-empty string"):
        validate_config_validations({"validation": {"contains": ""}})

    with pytest.raises(ValueError, match="collide with built-in"):
        validate_config_validations(
            {"prompts": [{"name": "quick-migration-check", "prompt": "hello"}]}
        )

    with pytest.raises(ValueError, match="collide with built-in"):
        validate_config_validations(
            {"prompts": [{"name": "reasoning", "prompt": "hello"}]}
        )


def test_load_config_accepts_model_alias_strings(tmp_path):
    path = tmp_path / "benchmark.json"
    path.write_text(
        '{"prompt":"hi","aliases":{"fast":{"model":"fake"}},"models":["fast"]}'
    )

    assert load_config(path)["models"] == ["fast"]


def test_load_config_resolves_prompt_file_relative_to_config(tmp_path):
    data_dir = tmp_path / "fixtures"
    data_dir.mkdir()
    (data_dir / "orders.csv").write_text("order_id,total\nA-1,20\nA-2,-5")
    path = tmp_path / "benchmark.json"
    path.write_text(
        '{"prompts":[{"name":"csv-review","prompt_file":"fixtures/orders.csv"}],'
        '"models":[{"model":"fake"}]}'
    )

    config = load_config(path)

    assert config["prompts"][0]["prompt"] == "order_id,total\nA-1,20\nA-2,-5"


def test_load_config_rejects_prompt_file_path_traversal(tmp_path):
    path = tmp_path / "benchmark.json"
    path.write_text(
        '{"prompts":[{"name":"csv-review","prompt_file":"../orders.csv"}],'
        '"models":[{"model":"fake"}]}'
    )

    with pytest.raises(ValueError, match="must stay within the config directory"):
        load_config(path)


def test_select_custom_prompt_applies_named_request_and_validation():
    config = {
        "prompts": [
            {
                "name": "csv-review",
                "prompt": "Review this CSV",
                "system_prompt": "Return JSON.",
                "request": {"temperature": 0, "max_output_tokens": 500},
                "validation": {"contains": "rows"},
            }
        ],
        "models": [{"model": "fake"}],
    }

    selected = select_custom_prompt(config, "csv-review")

    assert selected["prompt_name"] == "csv-review"
    assert selected["prompt"] == "Review this CSV"
    assert selected["request"]["system_prompt"] == "Return JSON."
    assert selected["request"]["max_output_tokens"] == 500
    assert selected["validation"] == {"contains": "rows"}


def test_custom_prompt_profile_converts_validation_to_single_case():
    profile = custom_prompt_profile(
        {
            "name": "source-to-quiz",
            "prompt": "Return JSON",
            "system_prompt": "Only JSON.",
            "request": {"max_output_tokens": 500},
            "validation": {"regex": '"questions"\\s*:\\s*\\['},
        }
    )

    assert profile["name"] == "source-to-quiz"
    assert profile["request"] == {"max_output_tokens": 500}
    assert profile["system_prompt"] == "Only JSON."
    assert profile["cases"] == [
        {
            "id": "source-to-quiz",
            "prompt": "Return JSON",
            "evaluator": {"type": "regex", "regex": '"questions"\\s*:\\s*\\['},
        }
    ]


def test_custom_prompt_profile_preserves_fenced_json_policy():
    profile = custom_prompt_profile(
        {
            "name": "parser-aligned-contract",
            "prompt": "Return JSON",
            "validation": {
                "json_schema": {"required": ["exclude"]},
                "allow_fenced_json": True,
            },
        }
    )

    assert profile["cases"][0]["evaluator"] == {
        "type": "json_schema",
        "schema": {"required": ["exclude"]},
        "allow_fenced_json": True,
    }


def test_validation_rejects_fenced_json_policy_without_a_json_validator():
    with pytest.raises(ValueError, match="allow_fenced_json requires a JSON validator"):
        validate_config_validations({"validation": {"allow_fenced_json": True}})


def test_custom_prompt_profile_combines_common_validators():
    profile = custom_prompt_profile(
        {
            "name": "contract",
            "prompt": "Return a short JSON object.",
            "validation": {
                "json_object": True,
                "no_markdown": True,
                "max_chars": 40,
            },
        }
    )

    assert profile["cases"][0]["evaluator"] == {
        "type": "all",
        "evaluators": [
            {"type": "json_object"},
            {"type": "no_markdown"},
            {"type": "max_chars", "maximum": 40},
        ],
    }


@pytest.mark.parametrize(
    ("validation", "message"),
    [
        ({"json_object": "yes"}, "json_object must be a boolean"),
        ({"exact_count": -1}, "exact_count must be a non-negative integer"),
        ({"exact_count": 2}, "exact_count requires json_array"),
        ({"allowed_values": []}, "allowed_values must be a non-empty list"),
        ({"numeric_tolerance": 1}, "numeric_tolerance requires numeric_answer"),
        ({"max_chars": 1.5}, "max_chars must be a non-negative integer"),
    ],
)
def test_common_validator_configuration_is_checked(validation, message):
    with pytest.raises(ValueError, match=message):
        validate_config_validations({"validation": validation})


def test_report_handles_missing_usage():
    result = {
        "benchmark": "test",
        "run_id": "abc",
        "timestamp": "2026-01-01T00:00:00Z",
        "prompt_sha256": "1234567890abcdef",
        "prompt_name": "csv-review",
        "models": [
            {
                "name": "model-a",
                "summary": {
                    "success_rate": 1,
                    "latency_seconds": {"p50": 1, "p95": 2},
                    "ttft_seconds": {"p50": None},
                    "output_tokens_per_second": {"p50": None},
                    "estimated_cost_usd": None,
                },
            }
        ],
    }
    rendered = report(result)
    assert "Prompt: **csv-review** (`1234567890ab`)" in rendered
    assert "| model-a | 100% | 1.000s | 2.000s | n/a | n/a | n/a |" in rendered


def test_plain_prompt_validation_failure_cannot_pass_or_be_recommended():
    result = run_benchmark(
        {
            "name": "plain-validation",
            "prompt": "Reply with ok.",
            "validation": {"contains": "required"},
            "models": [
                {
                    "name": "invalid-but-fast",
                    "provider": "mock",
                    "model": "local",
                    "response": "wrong",
                    "input_cost_per_million": 1,
                    "output_cost_per_million": 1,
                }
            ],
            "repetitions": 1,
            "warmups": 0,
        }
    )

    summary = result["models"][0]["summary"]
    assert summary["valid_output_rate"] == 0
    rendered = console_report(result)
    assert "invalid-but-fast" in rendered
    assert "FAIL" in rendered
    assert "| invalid-but-fast | FAIL | config prompt |" in report(result)
    assert (
        "Recommended: unavailable; no priced model passed every selected test."
        in rendered
    )
    assert (
        "Excluded from recommendations: **invalid-but-fast** (failed: config prompt)."
        in report(result)
    )


def test_plain_prompt_supports_exact_validation_and_rejects_unknown_rules():
    result = run_benchmark(
        {
            "prompt": "Reply with ok.",
            "validation": {"exact": "ok"},
            "models": [{"provider": "mock", "model": "local", "response": "not ok"}],
            "repetitions": 1,
            "warmups": 0,
        }
    )
    assert result["models"][0]["samples"][0]["valid_output"] is False

    with pytest.raises(ValueError, match="unknown validation keys: expected"):
        run_benchmark(
            {
                "prompt": "Reply with ok.",
                "validation": {"expected": "ok"},
                "models": [{"provider": "mock", "model": "local"}],
            }
        )


def test_unexpected_per_model_request_exception_is_saved_as_api_failure(monkeypatch):
    class Client:
        model = {"base_url": "https://example.test"}

        def __init__(self, fail):
            self.fail = fail

        def run(self, _prompt, _options):
            if self.fail:
                raise ValueError("host could not be resolved")
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 1,
                "input_tokens": 1,
                "output_tokens": 1,
                "response_chars": 2,
                "response": "ok",
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client",
        lambda model, _timeout: Client(model["model"] == "broken"),
    )
    result = run_benchmark(
        {
            "prompt": "Reply with ok.",
            "models": [
                {"provider": "openai", "model": "working"},
                {"provider": "openai", "model": "broken"},
            ],
            "repetitions": 1,
            "warmups": 0,
        }
    )

    assert [model["summary"]["failed"] for model in result["models"]] == [0, 1]
    assert result["models"][1]["samples"][0]["failure_category"] == "network"


def test_profile_run_groups_quality_and_operational_metrics(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            response = "billing"
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 10,
                "input_tokens": 5,
                "output_tokens": 1,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )
    result = run_benchmark(
        {
            "name": "profiles",
            "prompt": "legacy-required-prompt",
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "suite_repetitions": 1,
        },
        profile_selector="classification",
    )
    profile = result["models"][0]["profiles"][0]
    assert profile["name"] == "exact-routing-check"
    assert profile["summary"]["quality_score"] == 1 / 3
    assert profile["summary"]["valid_output_rate"] == 1 / 3
    assert len(profile["samples"]) == 3
    rendered = report(result)
    assert "| fake | exact-routing-check | 33% | 100% |" in rendered


def test_run_benchmark_reports_live_model_and_request_progress(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": 2,
                "response": "ok",
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )
    events = []

    run_benchmark(
        {
            "prompt": "test",
            "models": [
                {
                    "provider": "openai",
                    "model": "fake",
                    "input_cost_per_million": 1,
                    "output_cost_per_million": 2,
                }
            ],
            "warmups": 1,
            "repetitions": 2,
        },
        progress=events.append,
    )

    assert events[0] == {
        "type": "model_start",
        "model_index": 1,
        "model_total": 1,
        "provider": "openai",
        "model": "fake",
        "request_total": 2,
    }
    completed = [event for event in events if event["type"] == "request_complete"]
    assert [event["request_index"] for event in completed] == [1, 2]
    assert completed[0]["status"] == "ok"
    assert completed[0]["input_tokens"] == 10
    assert completed[0]["output_tokens"] == 2
    assert completed[0]["estimated_cost_usd"] == 0.000014
    assert events[-1]["type"] == "model_complete"
    assert events[-1]["input_tokens"] == 20
    assert events[-1]["output_tokens"] == 4
    assert events[-1]["estimated_cost_usd"] == pytest.approx(0.000028)
    result = run_benchmark(
        {
            "prompt": "test",
            "models": [
                {
                    "provider": "openai",
                    "model": "fake",
                    "input_cost_per_million": 1,
                    "output_cost_per_million": 2,
                }
            ],
            "warmups": 1,
            "repetitions": 2,
        }
    )
    assert result["models"][0]["warmup_summary"]["requests"] == 1
    assert result["total_estimated_cost_usd"] == pytest.approx(0.000042)


def test_profile_progress_reports_invalid_outputs_separately(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": 5,
                "response": "wrong",
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )
    events = []

    run_benchmark(
        {
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "suite_repetitions": 1,
        },
        profile_selector="classification",
        progress=events.append,
    )

    completed = [event for event in events if event["type"] == "request_complete"]
    assert completed[0]["status"] == "ok"
    assert completed[0]["valid_output"] is False
    assert completed[0]["evaluation_error"] == "exact match failed"
    assert events[-1]["type"] == "model_complete"
    assert events[-1]["failed"] == 0
    assert events[-1]["invalid_outputs"] == 3


def test_load_profile_progress_includes_concurrency_level(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": 9,
                "response": "benchmark",
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )
    events = []

    run_benchmark(
        {
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "suite_repetitions": 1,
        },
        profile_selector="load",
        progress=events.append,
    )

    phases = [event["phase"] for event in events if event["type"] == "request_complete"]
    assert "concurrency-health-check/load-short@c1" in phases
    assert "concurrency-health-check/load-short@c5" in phases
    assert "concurrency-health-check/load-short@c10" in phases


def test_run_benchmark_can_select_profiles_from_config(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": 2,
                "response": "ok",
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )
    result = run_benchmark(
        {
            "prompt": "test",
            "profiles": "classification",
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "suite_repetitions": 1,
        }
    )
    profile_names = [profile["name"] for profile in result["models"][0]["profiles"]]
    assert profile_names == ["exact-routing-check"]
    assert result["settings"]["profiles"] == ["exact-routing-check"]


def test_run_benchmark_can_mix_builtin_and_custom_prompt_profiles(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            response = '{"questions":[]}' if "quiz" in prompt else "billing"
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )
    result = run_benchmark(
        {
            "prompts": [
                {
                    "name": "source-to-quiz",
                    "prompt": "make quiz",
                    "validation": {"regex": '"questions"\\s*:\\s*\\['},
                }
            ],
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "suite_repetitions": 1,
        },
        profile_selector="classification,source-to-quiz",
    )

    profiles = result["models"][0]["profiles"]
    assert [profile["name"] for profile in profiles] == [
        "exact-routing-check",
        "source-to-quiz",
    ]
    assert profiles[1]["summary"]["quality_score"] == 1
    assert result["settings"]["profiles"] == [
        "exact-routing-check",
        "source-to-quiz",
    ]


def test_custom_prompt_profile_presets_expand_into_request_options(monkeypatch):
    captured_options = []

    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            captured_options.append(dict(options))
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": 16,
                "response": '{"questions":[]}',
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )

    run_benchmark(
        {
            "models": [{"provider": "gemini", "model": "fake"}],
            "warmups": 0,
            "suite_repetitions": 1,
            "prompts": [
                {
                    "name": "source-to-quiz",
                    "prompt": "Make a quiz",
                    "presets": ["structured"],
                    "request": {"max_output_tokens": 1200},
                    "validation": {"contains": "questions"},
                }
            ],
        },
        profile_selector="source-to-quiz",
    )

    assert captured_options[0]["max_output_tokens"] == 1200
    assert captured_options[0]["provider_options"]["gemini"]["generationConfig"] == {
        "responseMimeType": "application/json",
        "thinkingConfig": {"includeThoughts": False},
    }


def test_profile_runs_can_save_only_failed_responses(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def __init__(self):
            self.calls = 0

        def run(self, prompt, options):
            self.calls += 1
            response = "wrong" if self.calls == 1 else "billing"
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )

    result = run_benchmark(
        {
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "suite_repetitions": 2,
            "save_responses": "failures",
            "prompts": [
                {
                    "name": "custom-check",
                    "prompt": "Return billing",
                    "validation": {"contains": "billing"},
                }
            ],
        },
        profile_selector="custom-check",
    )

    samples = result["models"][0]["profiles"][0]["samples"]
    failed, passed = sorted(samples, key=lambda sample: sample["valid_output"])
    assert failed["response"] == "wrong"
    assert "response" not in passed


def test_run_benchmark_records_failure_reasons_and_can_save_only_failures(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def __init__(self):
            self.calls = 0

        def run(self, prompt, options):
            self.calls += 1
            response = "bad" if self.calls == 1 else "expected"
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )
    result = run_benchmark(
        {
            "prompt": "test",
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "repetitions": 2,
            "save_responses": "failures",
            "validation": {"contains": "expected"},
        }
    )
    model = result["models"][0]
    assert model["summary"]["failed"] == 0
    assert model["summary"]["failure_reasons"] == {}
    failed, passed = sorted(model["samples"], key=lambda sample: sample["valid_output"])
    assert failed["valid_output"] is False
    assert failed["evaluation_error"] == "response did not contain 'expected'"
    assert failed["response"] == "bad"
    assert "response" not in passed
    assert result["source_config"]["save_responses"] == "failures"


def test_run_benchmark_stop_on_any_fail_stops_after_failed_model(monkeypatch):
    class FakeClient:
        def __init__(self, model):
            self.model = {"base_url": "https://example.test", **model}

        def run(self, prompt, options):
            response = "bad" if self.model["model"] == "bad-model" else "expected"
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient(model)
    )

    result = run_benchmark(
        {
            "prompt": "test",
            "models": [
                {"provider": "openai", "model": "bad-model"},
                {"provider": "openai", "model": "good-model"},
            ],
            "warmups": 0,
            "repetitions": 1,
            "validation": {"contains": "expected"},
            "stop_on": "any-fail",
        }
    )

    assert [model["model"] for model in result["models"]] == ["bad-model"]
    assert result["models"][0]["summary"]["failed"] == 0
    assert result["models"][0]["samples"][0]["valid_output"] is False


def test_run_benchmark_stop_on_api_error_ignores_test_failures(monkeypatch):
    class FakeClient:
        def __init__(self, model):
            self.model = {"base_url": "https://example.test", **model}

        def run(self, prompt, options):
            response = "wrong" if self.model["model"] == "weak-model" else "expected"
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient(model)
    )

    result = run_benchmark(
        {
            "prompt": "test",
            "models": [
                {"provider": "openai", "model": "weak-model"},
                {"provider": "openai", "model": "good-model"},
            ],
            "warmups": 0,
            "repetitions": 1,
            "validation": {"contains": "expected"},
            "stop_on": "api-error",
        }
    )

    assert [model["model"] for model in result["models"]] == [
        "weak-model",
        "good-model",
    ]
    assert result["models"][0]["summary"]["failed"] == 0
    assert result["models"][0]["samples"][0]["valid_output"] is False


def test_run_benchmark_stop_on_test_fail_stops_on_invalid_output(monkeypatch):
    class FakeClient:
        def __init__(self, model):
            self.model = {"base_url": "https://example.test", **model}

        def run(self, prompt, options):
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": 5,
                "response": "wrong",
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient(model)
    )

    result = run_benchmark(
        {
            "prompt": "test",
            "models": [
                {"provider": "openai", "model": "weak-model"},
                {"provider": "openai", "model": "good-model"},
            ],
            "warmups": 0,
            "repetitions": 1,
            "validation": {"contains": "expected"},
            "stop_on": "test-fail",
        }
    )

    assert [model["model"] for model in result["models"]] == ["weak-model"]


def test_failed_validation_keeps_response_preview_without_full_response(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            response = "I cannot produce that shape."
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )

    result = run_benchmark(
        {
            "prompt": "test",
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "repetitions": 1,
            "validation": {"contains": "questions"},
        }
    )

    sample = result["models"][0]["samples"][0]
    assert sample["ok"] is True
    assert sample["valid_output"] is False
    assert sample["response_preview"] == "I cannot produce that shape."
    assert "response" not in sample


def test_failed_profile_validation_keeps_response_preview(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            response = "wrong"
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )

    result = run_benchmark(
        {
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "suite_repetitions": 1,
        },
        profile_selector="classification",
    )

    sample = result["models"][0]["profiles"][0]["samples"][0]
    assert sample["ok"] is True
    assert sample["valid_output"] is False
    assert sample["response_preview"] == "wrong"
    assert "response" not in sample


def test_validation_failure_summary_hints_use_real_pipeline_samples(
    monkeypatch, tmp_path
):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            response = "```json\n{}\n```"
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 5,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )

    result = run_benchmark(
        {
            "prompt": "Return JSON",
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "repetitions": 1,
            "save_responses": "failures",
            "validation": {"json_schema": {"required": ["questions"]}},
        }
    )

    assert result["models"][0]["summary"]["failure_hints"] == [
        "response appears to be fenced Markdown instead of raw output"
    ]
    sample = result["models"][0]["samples"][0]
    assert sample["json_parsing_policy"] == "raw_json"
    assert sample["response_preview"] == "```json {} ```"
    assert sample["response"] == "```json\n{}\n```"
    artifact = json.loads(save_result(result, tmp_path).read_text())
    saved_sample = artifact["models"][0]["samples"][0]
    assert saved_sample["json_parsing_policy"] == "raw_json"
    assert saved_sample["response"] == "```json\n{}\n```"


def test_custom_fenced_json_contract_keeps_successful_output_out_of_artifacts(
    monkeypatch,
):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            response = '```json\n{"exclude":[2,3,4]}\n```'
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 5,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )

    result = run_benchmark(
        {
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "suite_repetitions": 1,
            "prompts": [
                {
                    "name": "exclude-contract",
                    "prompt": "Return JSON",
                    "validation": {
                        "json_schema": {
                            "type": "object",
                            "required": ["exclude"],
                        },
                        "allow_fenced_json": True,
                    },
                }
            ],
        },
        profile_selector="exclude-contract",
    )

    sample = result["models"][0]["profiles"][0]["samples"][0]
    assert sample["valid_output"] is True
    assert sample["json_parsing_policy"] == "single_fenced_block"
    assert "response" not in sample


def test_profile_validation_failure_summary_hints_use_real_pipeline_samples(
    monkeypatch,
):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            response = "Note: I must output only JSON."
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 5,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )

    result = run_benchmark(
        {
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "suite_repetitions": 1,
            "prompts": [
                {
                    "name": "source-to-quiz",
                    "prompt": "Make quiz",
                    "validation": {"regex": '"questions"\\s*:\\s*\\['},
                }
            ],
        },
        profile_selector="source-to-quiz",
    )

    assert result["models"][0]["profiles"][0]["summary"]["failure_hints"] == [
        "reasoning or commentary appeared before the expected answer"
    ]


def test_report_ends_with_executive_summary_categories():
    def summary(latency, cost, success=1):
        return {
            "requests": 2,
            "successful": round(2 * success),
            "failed": 2 - round(2 * success),
            "success_rate": success,
            "latency_seconds": {"mean": latency, "p50": latency, "p95": latency},
            "ttft_seconds": {"p50": latency},
            "output_tokens_per_second": {"p50": 10},
            "input_tokens": 10,
            "output_tokens": 5,
            "estimated_cost_usd": cost,
        }

    result = {
        "benchmark": "ranked",
        "run_id": "abc",
        "timestamp": "2026-01-01T00:00:00Z",
        "prompt_sha256": "1234567890abcdef",
        "total_estimated_cost_usd": 0.0045,
        "models": [
            {"name": "balanced", "summary": summary(1.5, 0.001)},
            {"name": "fast", "summary": summary(1, 0.003)},
            {"name": "cheap-unreliable", "summary": summary(3, 0.0005, 0.5)},
        ],
    }

    rendered = report(result)
    assert "## Executive summary" in rendered
    assert "- Fastest: **fast** — 1.000s mean latency." in rendered
    assert "- Cheapest: **balanced** — $0.001000 total." in rendered
    assert "- Best value: **balanced**" in rendered
    assert (
        "- Recommended: **balanced** — passed every selected test and led the "
        "qualified value ranking."
    ) in rendered
    assert (
        "- Excluded from recommendations: **cheap-unreliable** (failed: config prompt)."
    ) in rendered
    assert "- Total spent: **$0.004500** including warmups." in rendered
    assert rendered.rstrip().endswith(
        "Value equally weights reliability, relative speed, and relative cost "
        "among models that passed every selected test."
    )


def test_executive_summary_excludes_models_that_fail_any_selected_profile():
    def summary(latency, cost, reliability):
        return {
            "requests": 3,
            "successful": 3,
            "failed": 0,
            "success_rate": 1,
            "valid_output_rate": reliability,
            "quality_score": reliability,
            "latency_seconds": {"mean": latency, "p50": latency, "p95": latency},
            "ttft_seconds": {"p50": latency},
            "output_tokens_per_second": {"p50": 10},
            "input_tokens": 10,
            "output_tokens": 5,
            "estimated_cost_usd": cost,
        }

    result = {
        "benchmark": "profile-gate",
        "run_id": "abc",
        "timestamp": "2026-01-01T00:00:00Z",
        "prompt_sha256": "1234567890abcdef",
        "models": [
            {
                "name": "fast-cheap-but-failed",
                "profiles": [
                    {"name": "chat-fast", "summary": summary(0.5, 0.00001, 1)},
                    {
                        "name": "structured-extraction",
                        "summary": summary(0.5, 0.00001, 2 / 3),
                    },
                ],
            },
            {
                "name": "qualified",
                "profiles": [
                    {"name": "chat-fast", "summary": summary(1, 0.0001, 1)},
                    {
                        "name": "structured-extraction",
                        "summary": summary(1, 0.0001, 1),
                    },
                ],
            },
        ],
    }

    rendered = report(result)

    assert "- Fastest: **qualified** — 1.000s mean latency." in rendered
    assert "- Cheapest: **qualified** — $0.000200 total." in rendered
    assert "- Best value: **qualified**" in rendered
    assert (
        "- Excluded from recommendations: **fast-cheap-but-failed** "
        "(failed: structured-extraction)."
    ) in rendered


def test_report_includes_pricing_warnings():
    result = {
        "benchmark": "pricing",
        "run_id": "abc",
        "timestamp": "2026-01-01T00:00:00Z",
        "prompt_sha256": "1234567890abcdef",
        "pricing_warnings": [
            {
                "provider": "openai_compatible",
                "model": "local",
                "message": "pricing is unknown",
            }
        ],
        "models": [
            {
                "name": "local",
                "summary": {
                    "requests": 1,
                    "successful": 1,
                    "failed": 0,
                    "success_rate": 1,
                    "latency_seconds": {"mean": 1, "p50": 1, "p95": 1},
                    "ttft_seconds": {"p50": 0.5},
                    "output_tokens_per_second": {"p50": 10},
                    "estimated_cost_usd": None,
                },
            }
        ],
    }

    rendered = report(result)

    assert "## Pricing warnings" in rendered
    assert "openai_compatible/local: pricing is unknown" in rendered


def test_zero_reliability_model_cannot_rank_as_cheapest():
    result = {
        "benchmark": "failed-cheap",
        "run_id": "abc",
        "timestamp": "2026-01-01T00:00:00Z",
        "prompt_sha256": "1234567890abcdef",
        "models": [
            {
                "name": "failed",
                "summary": {
                    "requests": 1,
                    "successful": 0,
                    "failed": 1,
                    "success_rate": 0,
                    "latency_seconds": {"mean": None, "p50": None, "p95": None},
                    "ttft_seconds": {"p50": None},
                    "output_tokens_per_second": {"p50": None},
                    "estimated_cost_usd": 0,
                },
            },
            {
                "name": "working",
                "summary": {
                    "requests": 1,
                    "successful": 1,
                    "failed": 0,
                    "success_rate": 1,
                    "latency_seconds": {"mean": 1, "p50": 1, "p95": 1},
                    "ttft_seconds": {"p50": 0.5},
                    "output_tokens_per_second": {"p50": 10},
                    "estimated_cost_usd": 0.001,
                },
            },
        ],
    }
    rendered = report(result)
    assert "- Cheapest: **working** — $0.001000 total." in rendered


def test_low_reliability_model_cannot_rank_as_best_value():
    def summary(latency, cost, reliability):
        return {
            "requests": 3,
            "successful": 3,
            "failed": 0,
            "success_rate": 1,
            "valid_output_rate": reliability,
            "latency_seconds": {"mean": latency, "p50": latency, "p95": latency},
            "ttft_seconds": {"p50": latency},
            "output_tokens_per_second": {"p50": 10},
            "input_tokens": 10,
            "output_tokens": 5,
            "estimated_cost_usd": cost,
        }

    result = {
        "benchmark": "ranked",
        "run_id": "abc",
        "timestamp": "2026-01-01T00:00:00Z",
        "prompt_sha256": "1234567890abcdef",
        "models": [
            {"name": "reliable", "summary": summary(2, 0.01, 1)},
            {"name": "cheap-weak", "summary": summary(1, 0.0001, 0.33)},
        ],
    }

    rendered = report(result)

    assert "- Best value: **reliable**" in rendered
    assert "- Best value: **cheap-weak**" not in rendered


def test_console_report_uses_aligned_terminal_table_and_optional_color():
    result = {
        "benchmark": "console",
        "run_id": "abc",
        "timestamp": "2026-01-01T00:00:00Z",
        "prompt_sha256": "1234567890abcdef",
        "models": [
            {
                "name": "model-a",
                "summary": {
                    "requests": 1,
                    "successful": 1,
                    "failed": 0,
                    "success_rate": 1,
                    "latency_seconds": {"mean": 1, "p50": 1, "p95": 1},
                    "ttft_seconds": {"p50": 0.5},
                    "output_tokens_per_second": {"p50": 10},
                    "input_tokens": 5,
                    "output_tokens": 2,
                    "estimated_cost_usd": 0.0001,
                },
            }
        ],
    }

    plain = console_report(result)
    assert "┌" in plain and "│ Model" in plain
    assert "| Model |" not in plain
    assert "\x1b[" not in plain
    assert "Executive summary" in plain
    assert "\x1b[" in console_report(result, color=True)


def test_console_report_visually_separates_results_quality_and_decision():
    result = {
        "benchmark": "console",
        "run_id": "abc",
        "timestamp": "2026-01-01T00:00:00Z",
        "prompt_sha256": "1234567890abcdef",
        "models": [],
    }

    rendered = console_report(result, color=True)

    assert "=== RESULTS ===" in rendered
    assert "=== QUALITY GATE ===" in rendered
    assert "=== DECISION ===" in rendered


def test_console_report_includes_pass_fail_dashboard():
    result = {
        "benchmark": "smoke",
        "run_id": "run-1",
        "timestamp": "2026-07-12T00:00:00+00:00",
        "prompt_sha256": "abc123",
        "models": [
            {
                "name": "passing",
                "summary": {
                    "requests": 1,
                    "successful": 1,
                    "failed": 0,
                    "success_rate": 1,
                    "latency_seconds": {"mean": 1, "p50": 1, "p95": 1},
                    "ttft_seconds": {"p50": 0.2},
                    "output_tokens_per_second": {"p50": 10},
                    "estimated_cost_usd": 0.001,
                },
            },
            {
                "name": "failing",
                "summary": {
                    "requests": 1,
                    "successful": 0,
                    "failed": 1,
                    "success_rate": 0,
                    "latency_seconds": {"mean": None, "p50": None, "p95": None},
                    "ttft_seconds": {"p50": None},
                    "output_tokens_per_second": {"p50": None},
                    "estimated_cost_usd": 0.001,
                    "failure_reasons": {"rate limited": 1},
                },
            },
        ],
    }

    rendered = console_report(result)

    assert "Pass/fail dashboard" in rendered
    assert "passing" in rendered
    assert "PASS" in rendered
    assert "failing" in rendered
    assert "FAIL" in rendered
    assert "rate limited" in rendered


def test_save_result_also_writes_markdown_report(tmp_path):
    result = {
        "benchmark": "saved",
        "run_id": "abc12345",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "prompt_sha256": "1234567890abcdef",
        "models": [],
    }
    output_dir = tmp_path / "results"
    json_path = save_result(result, output_dir)
    markdown_path = json_path.with_suffix(".md")
    assert json_path.exists()
    assert markdown_path.exists()
    assert markdown_path.read_text().startswith("# saved\n")
    assert stat.S_IMODE(json_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(markdown_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700


def test_save_result_redacts_secret_values_from_artifacts(tmp_path):
    result = {
        "benchmark": "saved",
        "run_id": "abc12345",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "prompt_sha256": "1234567890abcdef",
        "models": [
            {
                "name": "private",
                "summary": {
                    "requests": 1,
                    "successful": 0,
                    "failed": 1,
                    "success_rate": 0,
                    "latency_seconds": {"mean": None, "p50": None, "p95": None},
                    "ttft_seconds": {"p50": None},
                    "output_tokens_per_second": {"p50": None},
                    "estimated_cost_usd": 0.001,
                    "failure_reasons": {
                        "Authorization Bearer saved-redaction-secret": 1
                    },
                },
            }
        ],
        "source_config": {
            "models": [
                {
                    "model": "private",
                    "headers": {"Authorization": "Bearer saved-redaction-secret"},
                }
            ],
            "api_token": "saved-redaction-secret",
        },
    }
    output_dir = tmp_path / "results"

    json_path = save_result(result, output_dir)

    for path in [
        json_path,
        json_path.with_suffix(".md"),
        json_path.with_suffix(".summary.md"),
    ]:
        assert "saved-redaction-secret" not in path.read_text()


def test_save_responses_true_keeps_every_plain_prompt_response(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": 2,
                "response": "ok",
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )

    result = run_benchmark(
        {
            "prompt": "test",
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "repetitions": 1,
            "save_responses": True,
        }
    )

    assert result["models"][0]["samples"][0]["response"] == "ok"


def test_save_responses_unrecognized_value_drops_the_response(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": 2,
                "response": "ok",
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )

    result = run_benchmark(
        {
            "prompt": "test",
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "repetitions": 1,
            "save_responses": "always",
        }
    )

    assert "response" not in result["models"][0]["samples"][0]


def test_fail_fast_stops_on_first_failed_model_without_explicit_stop_on(monkeypatch):
    class FakeClient:
        def __init__(self, model):
            self.model = {"base_url": "https://example.test", **model}

        def run(self, prompt, options):
            response = "bad" if self.model["model"] == "bad-model" else "expected"
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": len(response),
                "response": response,
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient(model)
    )

    result = run_benchmark(
        {
            "prompt": "test",
            "models": [
                {"provider": "openai", "model": "bad-model"},
                {"provider": "openai", "model": "good-model"},
            ],
            "warmups": 0,
            "repetitions": 1,
            "validation": {"contains": "expected"},
            "fail_fast": True,
        }
    )

    assert [model["model"] for model in result["models"]] == ["bad-model"]


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ('{"models":[{"model":"fake"}]}', "requires 'prompt' or 'prompts'"),
        (
            '{"prompts":"nope","models":[{"model":"fake"}]}',
            "'prompts' must be a list",
        ),
        (
            '{"prompts":["not-an-object"],"models":[{"model":"fake"}]}',
            r"prompts\[0\] must be an object",
        ),
        (
            '{"prompts":[{"prompt":"hi"}],"models":[{"model":"fake"}]}',
            r"prompts\[0\] requires 'name'",
        ),
        (
            '{"prompts":[{"name":"x","prompt":"hi","prompt_file":"f.txt"}],'
            '"models":[{"model":"fake"}]}',
            "must use either 'prompt' or 'prompt_file'",
        ),
        (
            '{"prompts":[{"name":"x","prompt_file":""}],"models":[{"model":"fake"}]}',
            "requires a non-empty 'prompt_file'",
        ),
        (
            '{"prompts":[{"name":"x","prompt_file":"/etc/passwd"}],'
            '"models":[{"model":"fake"}]}',
            "prompt_file must be relative",
        ),
        (
            '{"prompts":[{"name":"x","prompt_file":"missing.txt"}],'
            '"models":[{"model":"fake"}]}',
            "does not exist or is not a file",
        ),
        (
            '{"prompts":[{"name":"x"}],"models":[{"model":"fake"}]}',
            "requires a non-empty 'prompt'",
        ),
        (
            '{"prompts":[{"name":"x","prompt":"a"},{"name":"x","prompt":"b"}],'
            '"models":[{"model":"fake"}]}',
            "custom prompt names must be unique",
        ),
        ('{"prompt":"hi"}', "requires 'models' or 'discovery'"),
        (
            '{"prompt":"hi","models":["missing-alias"]}',
            "unknown alias 'missing-alias'",
        ),
        (
            '{"prompt":"hi","models":[{"name":"x"}]}',
            r"models\[0\] requires 'model'",
        ),
    ],
)
def test_load_config_rejects_malformed_configs(tmp_path, payload, match):
    path = tmp_path / "benchmark.json"
    path.write_text(payload)

    with pytest.raises(ValueError, match=match):
        load_config(path)


def test_select_custom_prompt_rejects_unknown_name_and_lists_available():
    config = {"prompts": [{"name": "csv-review", "prompt": "Review this CSV"}]}

    with pytest.raises(
        ValueError, match="unknown custom prompt 'missing'; choose csv-review"
    ):
        select_custom_prompt(config, "missing")


def test_custom_prompt_profile_maps_exact_validation():
    profile = custom_prompt_profile(
        {"name": "label", "prompt": "Classify", "validation": {"exact": "billing"}}
    )
    assert profile["cases"][0]["evaluator"] == {"type": "exact", "expected": "billing"}


def test_select_test_profiles_rejects_duplicate_and_colliding_custom_prompt_names():
    with pytest.raises(ValueError, match="duplicate custom prompt names: dup"):
        select_test_profiles(
            {
                "prompts": [
                    {"name": "dup", "prompt": "a"},
                    {"name": "dup", "prompt": "b"},
                ]
            },
            "dup",
        )

    with pytest.raises(ValueError, match="collide with built-in profiles"):
        select_test_profiles(
            {"prompts": [{"name": "numeric-instruction-check", "prompt": "a"}]},
            "numeric-instruction-check",
        )


def test_select_test_profiles_rejects_unknown_profile_names():
    with pytest.raises(ValueError, match="unknown profiles: bogus"):
        select_test_profiles({}, "bogus")


def test_select_test_profiles_deduplicates_selected_custom_prompts_in_order():
    profiles = select_test_profiles(
        {"prompts": [{"name": "greet", "prompt": "Say hello"}]},
        "greet,greet",
    )

    assert [profile["name"] for profile in profiles] == ["greet"]


def test_plain_prompt_regex_validation_records_the_failure_reason(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": 2,
                "response": "no match here",
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )

    result = run_benchmark(
        {
            "prompt": "test",
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "repetitions": 1,
            "validation": {"regex": r"^\d+$"},
        }
    )

    sample = result["models"][0]["samples"][0]
    assert sample["valid_output"] is False
    assert "did not match regex" in sample["evaluation_error"]


def test_validate_config_validations_allows_none_and_rejects_non_dict_validation():
    validate_config_validations({"validation": None})

    with pytest.raises(ValueError, match="validation must be an object"):
        validate_config_validations({"validation": "not-a-dict"})


def test_validate_config_validations_rejects_duplicate_prompt_names():
    with pytest.raises(ValueError, match="duplicate custom prompt names: dup"):
        validate_config_validations(
            {
                "prompts": [
                    {"name": "dup", "prompt": "a"},
                    {"name": "dup", "prompt": "b"},
                ]
            }
        )


def test_client_creation_failure_falls_back_to_unavailable_client(monkeypatch):
    def _raise(model, timeout):
        raise OSError("could not connect")

    monkeypatch.setattr("llm_preflight.runner.create_client", _raise)

    result = run_benchmark(
        {
            "prompt": "test",
            "models": [
                {"provider": "openai", "model": "fake", "base_url": "https://x.test"}
            ],
            "warmups": 0,
            "repetitions": 1,
        }
    )

    model = result["models"][0]
    assert model["base_url"] == "https://x.test"
    sample = model["samples"][0]
    assert sample["ok"] is False
    assert "could not connect" in sample["error"]


def test_profile_progress_callback_returns_none_without_a_callback():
    assert _profile_progress_callback(None, "quick-migration-check") is None


def test_run_profiles_execute_one_warmup_request_per_profile(monkeypatch):
    class FakeClient:
        model = {"base_url": "https://example.test"}

        def run(self, prompt, options):
            return {
                "ok": True,
                "latency_seconds": 1,
                "ttft_seconds": 0.1,
                "output_tokens_per_second": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "response_chars": 7,
                "response": "billing",
                "error": None,
            }

    monkeypatch.setattr(
        "llm_preflight.runner.create_client", lambda model, timeout: FakeClient()
    )

    result = run_benchmark(
        {
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 1,
            "suite_repetitions": 1,
        },
        profile_selector="classification",
    )

    assert result["models"][0]["warmup_summary"]["requests"] == 1


def test_run_benchmark_requires_a_prompt_or_selected_profiles():
    with pytest.raises(ValueError, match="select a custom prompt"):
        run_benchmark({"models": [{"model": "fake"}]})


def test_run_benchmark_requires_at_least_one_resolved_model():
    with pytest.raises(ValueError, match="model discovery returned no models"):
        run_benchmark({"prompt": "hi", "models": []})


def test_failed_tests_falls_back_to_dash_when_nothing_specific_is_recorded():
    assert _failed_tests({}) == "-"


def test_console_report_profile_mode_renders_quality_and_reliability_columns():
    result = {
        "benchmark": "console-profiles",
        "run_id": "abc",
        "timestamp": "2026-01-01T00:00:00Z",
        "prompt_sha256": "1234567890abcdef",
        "models": [
            {
                "name": "model-a",
                "profiles": [
                    {
                        "name": "exact-routing-check",
                        "summary": {
                            "requests": 3,
                            "quality_score": 1.0,
                            "valid_output_rate": 1.0,
                            "success_rate": 1.0,
                            "latency_seconds": {"p95": 1.2},
                            "ttft_seconds": {"p50": 0.1},
                            "output_tokens_per_second": {"p50": 10},
                            "estimated_cost_usd": 0.0002,
                        },
                    }
                ],
            }
        ],
    }

    rendered = console_report(result)

    assert "Profile" in rendered
    assert "Reliable" in rendered
    assert "exact-routing-check" in rendered
    assert "100%" in rendered


def test_executive_summary_handles_zero_latency_mock_results():
    result = {
        "benchmark": "mock",
        "run_id": "zero",
        "timestamp": "2026-01-01T00:00:00Z",
        "prompt_sha256": "0123456789abcdef",
        "models": [
            {
                "name": "instant",
                "summary": {
                    "requests": 1,
                    "successful": 1,
                    "failed": 0,
                    "success_rate": 1,
                    "valid_output_rate": 1,
                    "latency_seconds": {"mean": 0.0, "p50": 0.0, "p95": 0.0},
                    "ttft_seconds": {"p50": 0.0},
                    "output_tokens_per_second": {"p50": None},
                    "estimated_cost_usd": 0.001,
                },
            }
        ],
    }

    assert "- Best value: **instant**" in report(result)
