import stat

import pytest

from llm_bench.runner import (
    console_report,
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
        "Value equally weights valid-output reliability, relative speed, and relative cost."
    )


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
