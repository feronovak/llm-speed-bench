import stat

import pytest

from llm_bench.runner import (
    console_report,
    custom_prompt_profile,
    load_config,
    report,
    run_benchmark,
    save_result,
    select_custom_prompt,
)


def test_load_config_accepts_named_prompts_without_legacy_prompt(tmp_path):
    path = tmp_path / "benchmark.json"
    path.write_text(
        '{"prompts":[{"name":"csv-review","prompt":"Review this CSV"}],'
        '"models":[{"model":"fake"}]}'
    )

    assert load_config(path)["prompts"][0]["name"] == "csv-review"


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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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
    assert profile["name"] == "classification"
    assert profile["summary"]["quality_score"] == 1 / 3
    assert profile["summary"]["valid_output_rate"] == 1 / 3
    assert len(profile["samples"]) == 3
    rendered = report(result)
    assert "| fake | classification | 33% | 100% |" in rendered


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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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
    assert "load/load-short@c1" in phases
    assert "load/load-short@c5" in phases
    assert "load/load-short@c10" in phases


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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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
    assert profile_names == ["classification"]
    assert result["settings"]["profiles"] == ["classification"]


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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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
        "classification",
        "source-to-quiz",
    ]
    assert profiles[1]["summary"]["quality_score"] == 1
    assert result["settings"]["profiles"] == ["classification", "source-to-quiz"]


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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient(model)
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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient(model)
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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient(model)
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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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


def test_validation_failure_summary_hints_use_real_pipeline_samples(monkeypatch):
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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
    )

    result = run_benchmark(
        {
            "prompt": "Return JSON",
            "models": [{"provider": "openai", "model": "fake"}],
            "warmups": 0,
            "repetitions": 1,
            "validation": {"json_schema": {"required": ["questions"]}},
        }
    )

    assert result["models"][0]["summary"]["failure_hints"] == [
        "response appears to be fenced Markdown instead of raw output"
    ]


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
        "llm_bench.runner.create_client", lambda model, timeout: FakeClient()
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
    assert "- Cheapest: **cheap-unreliable** — $0.000500 total." in rendered
    assert "- Best value: **balanced**" in rendered
    assert "- Total spent: **$0.004500** including warmups." in rendered
    assert rendered.rstrip().endswith(
        "Value equally weights valid-output reliability, relative speed, and relative cost "
        "among models with at least 80% reliability."
    )


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
