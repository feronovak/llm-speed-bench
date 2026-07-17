import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from llm_preflight import cli
from llm_preflight.cli import (
    catalog_output,
    format_progress_event,
    interactive_selection,
)


def test_interactive_selection_accepts_providers_families_profiles_and_repetitions(
    monkeypatch,
):
    models = [
        {"provider": "openai", "model": "gpt-5.5"},
        {"provider": "openai", "model": "gpt-5.4-mini"},
        {"provider": "openrouter", "model": "qwen/qwen3.7-plus"},
        {"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"},
    ]
    monkeypatch.setattr("llm_preflight.cli.resolve_models", lambda config: models)
    answers = iter(["openai,openrouter/qwen", "1,4", "2", "", "y"])

    selected = interactive_selection(
        {"prompt": "test", "models": models, "repetitions": 5},
        input_fn=lambda prompt: next(answers),
        output_fn=lambda text: None,
    )

    config, profiles = selected
    assert [model["model"] for model in config["models"]] == [
        "gpt-5.5",
        "gpt-5.4-mini",
        "qwen/qwen3.7-plus",
    ]
    assert config["discovery"] == []
    assert config["repetitions"] == 2
    assert config["suite_repetitions"] == 2
    assert profiles == "quick-migration-check,numeric-instruction-check"


def test_interactive_selection_can_cancel(monkeypatch):
    monkeypatch.setattr(
        "llm_preflight.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    answers = iter(["all", "", "", "", "n"])

    selected = interactive_selection(
        {"prompt": "test", "models": [{"model": "gpt-5.5"}]},
        input_fn=lambda prompt: next(answers),
        output_fn=lambda text: None,
    )

    assert selected is None


def test_interactive_all_selects_functional_tests_but_not_load(monkeypatch):
    models = [{"provider": "mock", "model": "local", "response": "ok"}]
    monkeypatch.setattr("llm_preflight.cli.resolve_models", lambda config: models)
    answers = iter(["all", "all", "", "4", "n"])
    output = []

    selected = interactive_selection(
        {"prompt": "test", "models": models},
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
    )

    assert selected is None
    assert any(
        "Tests: quick-migration-check,exact-routing-check,structured-output-check,numeric-instruction-check"
        in line
        for line in output
    )


def test_interactive_selection_clears_screen_at_start(monkeypatch):
    monkeypatch.setattr(
        "llm_preflight.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    answers = iter(["all", "", "", "", "n"])
    cleared = []

    interactive_selection(
        {"prompt": "test", "models": [{"model": "gpt-5.5"}]},
        input_fn=lambda prompt: next(answers),
        output_fn=lambda text: None,
        clear_fn=lambda: cleared.append(True),
    )

    assert cleared == [True]


def test_interactive_selection_can_color_distinct_sections(monkeypatch):
    monkeypatch.setattr(
        "llm_preflight.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    output = []
    answers = iter(["all", "", "", "", "n"])

    interactive_selection(
        {"prompt": "test", "models": [{"model": "gpt-5.5"}]},
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
        color=True,
        clear_fn=lambda: None,
    )

    assert any("\x1b[" in line and "Models" in line for line in output)
    assert any("\x1b[" in line and "1." in line for line in output)
    assert any("\x1b[" in line and "Repetitions" in line for line in output)
    assert any("\x1b[" in line and "Cancelled." in line for line in output)


def test_interactive_selection_separates_repetitions_section(monkeypatch):
    monkeypatch.setattr(
        "llm_preflight.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    output = []
    answers = iter(["all", "", "", "", "n"])

    interactive_selection(
        {"prompt": "test", "models": [{"model": "gpt-5.5"}]},
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
        clear_fn=lambda: None,
    )

    assert "=== Repetitions ===" in output


def test_interactive_selection_lists_and_selects_named_custom_prompt(monkeypatch):
    monkeypatch.setattr(
        "llm_preflight.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    output = []
    answers = iter(["all", "csv-review", "", "", "y"])

    selected = interactive_selection(
        {
            "prompts": [
                {"name": "csv-review", "prompt": "Review this CSV"},
                {"name": "long-summary", "prompt": "Summarize this document"},
            ],
            "models": [{"model": "gpt-5.5"}],
        },
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
    )

    config, profiles = selected
    assert profiles == "csv-review"
    assert "prompt_name" not in config
    assert any("csv-review" in line for line in output)
    assert any("long-summary" in line for line in output)


def test_interactive_selection_numbers_custom_prompts_after_builtin_profiles(
    monkeypatch,
):
    monkeypatch.setattr(
        "llm_preflight.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    output = []
    answers = iter(["all", "1,6,7", "1", "", "y"])

    selected = interactive_selection(
        {
            "prompts": [
                {"name": "csv-review", "prompt": "Review this CSV"},
                {"name": "source-to-quiz", "prompt": "Make a quiz"},
            ],
            "models": [{"model": "gpt-5.5"}],
        },
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
    )

    config, profiles = selected
    assert profiles == "quick-migration-check,csv-review,source-to-quiz"
    assert config["repetitions"] == 1
    assert any("6. csv-review" in line for line in output)
    assert any("7. source-to-quiz" in line for line in output)


def test_main_selects_named_custom_prompt_non_interactively(
    monkeypatch, tmp_path, capsys
):
    config = tmp_path / "benchmark.json"
    config.write_text(
        '{"prompts":[{"name":"csv-review","prompt":"Review this CSV"}],'
        '"models":[{"model":"fake"}]}'
    )
    captured_config = {}
    result = {"models": [{"summary": {"failed": 0}}]}

    def fake_run(value, **kwargs):
        captured_config.update(value)
        return result

    monkeypatch.setattr(cli, "run_benchmark", fake_run)
    monkeypatch.setattr(cli, "save_result", lambda *args: tmp_path / "result.json")
    monkeypatch.setattr(cli, "console_report", lambda *args, **kwargs: "rendered")
    monkeypatch.setattr(
        sys, "argv", ["llm-preflight", str(config), "--prompt", "csv-review"]
    )

    cli.main()

    assert captured_config["prompt_name"] == "csv-review"
    assert captured_config["prompt"] == "Review this CSV"
    assert capsys.readouterr().out.strip() == "rendered"


def test_main_allows_profiles_to_mix_builtin_and_custom_prompts(
    monkeypatch, tmp_path, capsys
):
    config = tmp_path / "benchmark.json"
    config.write_text(
        '{"prompts":[{"name":"csv-review","prompt":"Review this CSV"}],'
        '"models":[{"model":"fake"}]}'
    )
    captured = {}
    result = {"models": [{"summary": {"failed": 0}}]}

    def fake_run(value, **kwargs):
        captured.update(value=value, kwargs=kwargs)
        return result

    monkeypatch.setattr(cli, "run_benchmark", fake_run)
    monkeypatch.setattr(cli, "save_result", lambda *args: tmp_path / "result.json")
    monkeypatch.setattr(cli, "console_report", lambda *args, **kwargs: "rendered")
    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-preflight", str(config), "--profiles", "classification,csv-review"],
    )

    cli.main()

    assert captured["kwargs"]["profile_selector"] == "classification,csv-review"
    assert capsys.readouterr().out.strip() == "rendered"


def test_main_allows_tests_alias_for_profiles(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text(
        '{"prompts":[{"name":"csv-review","prompt":"Review this CSV"}],'
        '"models":[{"model":"fake"}]}'
    )
    captured = {}
    result = {"models": [{"summary": {"failed": 0}}]}

    def fake_run(value, **kwargs):
        captured.update(value=value, kwargs=kwargs)
        return result

    monkeypatch.setattr(cli, "run_benchmark", fake_run)
    monkeypatch.setattr(cli, "save_result", lambda *args: tmp_path / "result.json")
    monkeypatch.setattr(cli, "console_report", lambda *args, **kwargs: "rendered")
    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-preflight", str(config), "--tests", "classification,csv-review"],
    )

    cli.main()

    assert captured["kwargs"]["profile_selector"] == "classification,csv-review"
    assert capsys.readouterr().out.strip() == "rendered"


def test_main_rejects_profiles_and_tests_together(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            str(config),
            "--profiles",
            "chat-fast",
            "--tests",
            "reasoning",
        ],
    )

    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected parser error")

    assert "--profiles cannot be combined with --tests" in capsys.readouterr().err


def test_version_flag_prints_package_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["llm-preflight", "--version"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 0
    from llm_preflight import __version__

    assert capsys.readouterr().out.strip() == f"llm-preflight {__version__}"


def test_help_lists_tests_before_profiles(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["llm-preflight", "--help"])

    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("expected help to exit")

    output = capsys.readouterr().out
    assert output.index("--tests") < output.index("--profiles")


def test_help_describes_smoke_as_a_reduced_run(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["llm-preflight", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "reduced live benchmark" in output
    assert "no warmups" in output


def test_migration_check_dry_run_uses_fast_response_contract(
    monkeypatch, tmp_path, capsys
):
    config_path = tmp_path / "benchmark.json"
    config_path.write_text(
        json.dumps(
            {
                "name": "candidate-review",
                "prompt": "ignored by the migration check",
                "models": [{"provider": "mock", "model": "local", "response": "ok"}],
                "suite_repetitions": 4,
                "warmups": 2,
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-preflight", str(config_path), "--migration-check", "--dry-run", "--json"],
    )

    cli.main()

    plan = json.loads(capsys.readouterr().out)
    assert plan["benchmark"] == "candidate-review-migration-check"
    assert plan["tests"] == ["quick-migration-check"]
    assert plan["requests"] == 3


def test_migration_check_requires_a_benchmark_configuration(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "--quick",
            "Reply with ok.",
            "--models",
            "mock:local",
            "--migration-check",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert (
        "--migration-check requires a benchmark configuration"
        in capsys.readouterr().err
    )


def test_main_init_creates_a_no_key_mock_benchmark(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "first-benchmark.json"
    monkeypatch.setattr(sys, "argv", ["llm-preflight", "--init", str(config_path)])

    cli.main()

    config = json.loads(config_path.read_text())
    assert config["name"] == "first-run"
    assert config["models"] == [
        {"name": "local-mock", "provider": "mock", "model": "local", "response": "ok"}
    ]
    assert config["validation"] == {"exact": "ok"}
    assert config["warmups"] == 0
    output = capsys.readouterr().out
    assert "Created" in output
    assert f"Explore interactively: llm-preflight {config_path} --interactive" in output


def test_main_init_prints_module_commands_when_run_with_python_module(
    monkeypatch, tmp_path, capsys
):
    config_path = tmp_path / "first-benchmark.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [str(Path(cli.__file__)), "--init", str(config_path)],
    )

    cli.main()

    output = capsys.readouterr().out
    assert (
        f"Run the no-key demo: python3 -m llm_preflight {config_path} --no-save"
        in output
    )
    assert (
        f"Explore interactively: python3 -m llm_preflight {config_path} --interactive"
        in output
    )


def test_main_init_refuses_to_overwrite_a_config(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "benchmark.json"
    config_path.write_text('{"prompt":"keep this"}\n')
    monkeypatch.setattr(sys, "argv", ["llm-preflight", "--init", str(config_path)])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert config_path.read_text() == '{"prompt":"keep this"}\n'
    assert "already exists" in capsys.readouterr().err


def test_interactive_selection_shows_request_and_cost_estimate(monkeypatch):
    models = [
        {
            "provider": "openai",
            "model": "gpt-5.5",
            "input_cost_per_million": 1,
            "output_cost_per_million": 2,
        }
    ]
    monkeypatch.setattr("llm_preflight.cli.resolve_models", lambda config: models)
    output = []
    answers = iter(["all", "", "2", "", "n"])

    interactive_selection(
        {
            "prompt": "hello world",
            "models": models,
            "request": {"max_output_tokens": 10},
            "warmups": 0,
        },
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
    )

    assert any("2 nominal requests, up to 4 with retries" in line for line in output)
    assert any("$0.000044" in line for line in output)


def test_interactive_selection_explains_functional_tests_request_breakdown(monkeypatch):
    monkeypatch.setattr(
        "llm_preflight.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    output = []
    answers = iter(["all", "all", "1", "", "n"])

    interactive_selection(
        {"prompt": "test", "models": [{"model": "gpt-5.5"}], "warmups": 0},
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
    )

    assert any("quick-migration-check: 3" in line for line in output)
    assert not any("concurrency-health-check:" in line for line in output)
    assert any("12 nominal requests, up to 24 with retries" in line for line in output)
    breakdown_index = output.index("Request breakdown per model:")
    assert output[breakdown_index - 1] == ""


def test_interactive_selection_shows_colored_run_plan_and_status_meaning(monkeypatch):
    monkeypatch.setattr(
        "llm_preflight.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    output = []
    answers = iter(["all", "1", "1", "", "n"])

    interactive_selection(
        {"prompt": "test", "models": [{"model": "gpt-5.5"}], "warmups": 0},
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
        color=True,
        clear_fn=lambda: None,
    )

    assert any("\x1b[" in line and "=== Run Plan ===" in line for line in output)
    assert any(
        "API OK means the provider returned a response" in line for line in output
    )
    assert any(
        "TEST OK/FAIL means the evaluator accepted or rejected it" in line
        for line in output
    )
    assert any("Stop on:" in line and "any-fail" in line for line in output)


def test_interactive_selection_visually_separates_each_stage(monkeypatch):
    monkeypatch.setattr(
        "llm_preflight.cli.resolve_models",
        lambda config: [{"provider": "mock", "model": "local"}],
    )
    output = []
    answers = iter(["all", "", "", "", "n"])

    interactive_selection(
        {"prompt": "test", "models": [{"model": "local"}]},
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
        clear_fn=lambda: None,
    )

    assert ["=== Models ===", "=== Tests ===", "=== Repetitions ==="] == [
        line
        for line in output
        if line in {"=== Models ===", "=== Tests ===", "=== Repetitions ==="}
    ]
    assert "=== Stop Mode ===" in output
    assert "=== Run Plan ===" in output


def test_interactive_selection_accepts_stop_mode(monkeypatch):
    monkeypatch.setattr(
        "llm_preflight.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    answers = iter(["all", "1", "1", "2", "y"])

    selected = interactive_selection(
        {"prompt": "test", "models": [{"model": "gpt-5.5"}], "warmups": 0},
        input_fn=lambda prompt: next(answers),
        output_fn=lambda text: None,
    )

    config, _ = selected
    assert config["stop_on"] == "api-error"


def test_interactive_selection_defaults_to_failed_response_retention(monkeypatch):
    monkeypatch.setattr(
        "llm_preflight.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    answers = iter(["all", "", "", "", "y"])

    selected = interactive_selection(
        {"prompt": "test", "models": [{"model": "gpt-5.5"}]},
        input_fn=lambda prompt: next(answers),
        output_fn=lambda text: None,
    )

    config, _ = selected
    assert config["save_responses"] == "failures"


def test_interactive_progress_describes_model_request_status_tokens_and_cost():
    assert (
        format_progress_event(
            {
                "type": "model_start",
                "model_index": 2,
                "model_total": 4,
                "provider": "openrouter",
                "model": "qwen/latest",
                "request_total": 3,
            }
        )
        == "Model 2/4: openrouter — qwen/latest (3 requests)"
    )
    assert format_progress_event(
        {
            "type": "request_complete",
            "request_index": 1,
            "request_total": 3,
            "phase": "load/load-short@c5",
            "status": "error",
            "valid_output": False,
            "input_tokens": 12,
            "output_tokens": 0,
            "estimated_cost_usd": 0.000012,
            "error": "rate limited",
        }
    ) == (
        "  Request 1/3 [load/load-short@c5]: API FAIL (rate limited) | "
        "tokens in/out 12/0 | cost $0.000012"
    )
    assert format_progress_event(
        {
            "type": "request_complete",
            "request_index": 2,
            "request_total": 3,
            "phase": "reasoning/reason-sequence",
            "status": "ok",
            "valid_output": False,
            "evaluation_error": "numeric answer outside tolerance",
            "input_tokens": 39,
            "output_tokens": 95,
            "estimated_cost_usd": 0.000042,
        }
    ) == (
        "  Request 2/3 [reasoning/reason-sequence]: "
        "API OK / TEST FAIL (numeric answer outside tolerance) | "
        "tokens in/out 39/95 | cost $0.000042"
    )
    assert format_progress_event(
        {
            "type": "request_complete",
            "request_index": 3,
            "request_total": 3,
            "phase": "classification/class-billing",
            "status": "ok",
            "valid_output": True,
            "input_tokens": 33,
            "output_tokens": 1,
            "estimated_cost_usd": 0.000004,
        }
    ) == (
        "  Request 3/3 [classification/class-billing]: API OK / TEST OK | "
        "tokens in/out 33/1 | cost $0.000004"
    )
    assert format_progress_event(
        {
            "type": "request_complete",
            "request_index": 1,
            "request_total": 1,
            "phase": "source-to-quiz",
            "status": "error",
            "valid_output": False,
            "input_tokens": 12,
            "output_tokens": 4,
            "estimated_cost_usd": 0.000012,
            "error": "response did not match regex",
            "response_preview": "I cannot produce that shape.",
        }
    ).endswith(" | preview I cannot produce that shape.")


def test_interactive_progress_model_complete_separates_api_and_test_failures():
    assert format_progress_event(
        {
            "type": "model_complete",
            "requests": 28,
            "successful": 28,
            "failed": 0,
            "invalid_outputs": 4,
            "input_tokens": 592,
            "output_tokens": 113,
            "estimated_cost_usd": 0.000104,
        }
    ) == (
        "  Done: 28/28 API ok, 0 request errors, 4 invalid outputs | "
        "tokens in/out 592/113 | cost $0.000104"
    )


def test_catalog_output_does_not_expose_custom_headers():
    output = catalog_output(
        [
            {
                "provider": "openai_compatible",
                "model": "private",
                "headers": {
                    "Authorization": "Bearer catalog-redaction-secret",
                    "X-API-Key": "catalog-redaction-secret",
                },
                "api_token": "catalog-redaction-secret",
            }
        ]
    )
    assert "headers" not in output[0]
    assert "catalog-redaction-secret" not in str(output)


def test_main_catalog_prints_safe_json(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    monkeypatch.setattr(
        cli,
        "resolve_models",
        lambda value: [{"model": "fake", "headers": {"Authorization": "secret"}}],
    )
    monkeypatch.setattr(sys, "argv", ["llm-preflight", str(config), "--catalog"])
    cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output == [{"model": "fake"}]


def test_watch_new_initializes_then_reports_new_models(monkeypatch, tmp_path, capsys):
    config = tmp_path / "watch.json"
    config.write_text('{"prompt":"hello","models":[{"model":"old"}]}')
    snapshot = tmp_path / "snapshot.json"
    monkeypatch.setattr(
        cli,
        "resolve_models",
        lambda value: [{"provider": "openai", "model": "old"}],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "watch-new",
            str(config),
            "--snapshot",
            str(snapshot),
            "--json",
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["initialized"] is True

    monkeypatch.setattr(
        cli,
        "resolve_models",
        lambda value: [
            {"provider": "openai", "model": "old"},
            {"provider": "openai", "model": "new"},
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "watch-new",
            str(config),
            "--snapshot",
            str(snapshot),
            "--json",
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["diff"]["added"][0]["model"] == "new"


def test_first_watch_test_runs_discovered_models_and_bootstraps_snapshot(
    monkeypatch, tmp_path
):
    watch = tmp_path / "watch.json"
    watch.write_text('{"prompt":"hello","models":[{"model":"old"}]}')
    approved = tmp_path / "approved.json"
    approved.write_text('{"models": []}')
    snapshot = tmp_path / "snapshot.json"
    captured = []
    monkeypatch.setattr(
        cli, "resolve_models", lambda _value: [{"provider": "openai", "model": "new"}]
    )
    monkeypatch.setattr(
        cli,
        "run_benchmark",
        lambda config, **_kwargs: captured.append(config) or {"models": []},
    )
    monkeypatch.setattr(cli, "console_report", lambda _result: "done")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "watch-new",
            str(watch),
            "--against",
            str(approved),
            "--snapshot",
            str(snapshot),
            "--test",
            "--no-save",
        ],
    )

    cli.main()

    assert captured[0]["models"] == [{"provider": "openai", "model": "new"}]
    assert json.loads(snapshot.read_text())["models"][0]["model"] == "new"


def test_catalog_refresh_uses_the_catalog_command_family(monkeypatch, tmp_path, capsys):
    config = tmp_path / "watch.json"
    config.write_text('{"prompt":"hello","models":[{"model":"old"}]}')
    monkeypatch.setattr(
        cli, "resolve_models", lambda value: [{"provider": "openai", "model": "old"}]
    )
    monkeypatch.setattr(
        sys, "argv", ["llm-preflight", "catalog", "refresh", str(config), "--json"]
    )

    cli.main()

    assert json.loads(capsys.readouterr().out)["initialized"] is True


def test_catalog_init_creates_a_ready_local_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "benchmarks"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "catalog",
            "init",
            str(workspace),
            "--providers",
            "openai,anthropic",
        ],
    )

    cli.main()

    watch = json.loads((workspace / "watch.json").read_text())
    assert [item["provider"] for item in watch["discovery"]] == ["openai", "anthropic"]
    assert json.loads((workspace / "approved.json").read_text()) == {
        "models": [],
        "approvals": [],
    }
    assert (workspace / ".env.production").read_text() == Path(
        ".env.example"
    ).read_text()


def test_catalog_init_guides_new_users_to_all_providers_by_default(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "benchmarks"
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    monkeypatch.setattr(
        sys, "argv", ["llm-preflight", "catalog", "init", str(workspace)]
    )

    cli.main()

    watch = json.loads((workspace / "watch.json").read_text())
    assert [item["provider"] for item in watch["discovery"]] == [
        "openai",
        "anthropic",
        "gemini",
        "xai",
        "openrouter",
    ]


def test_catalog_init_checks_an_existing_workspace_before_provider_setup(
    monkeypatch, tmp_path, capsys
):
    workspace = tmp_path / "benchmarks"
    workspace.mkdir()
    (workspace / "watch.json").write_text('{"name":"keep"}\n')
    answers = iter([""])
    prompts = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt: prompts.append(prompt) or next(answers)
    )
    monkeypatch.setattr(
        sys, "argv", ["llm-preflight", "catalog", "init", str(workspace)]
    )

    cli.main()

    assert prompts == [
        "A catalog workspace already exists. Rewrite watch settings and keep approved models, keys, and results? [y/N]: "
    ]
    assert json.loads((workspace / "watch.json").read_text()) == {"name": "keep"}
    assert "Kept existing catalog workspace" in capsys.readouterr().out


def test_catalog_init_reuses_an_existing_parent_env_file(monkeypatch, tmp_path):
    parent_env = tmp_path / ".env.production"
    parent_env.write_text('OPENAI_API_KEY="existing-key"\n')
    workspace = tmp_path / "benchmarks"
    monkeypatch.setattr("builtins.input", lambda prompt: "openai")
    monkeypatch.setattr(
        sys, "argv", ["llm-preflight", "catalog", "init", str(workspace)]
    )

    cli.main()

    workspace_env = workspace / ".env.production"
    assert workspace_env.is_symlink()
    assert workspace_env.resolve() == parent_env
    assert workspace_env.read_text() == parent_env.read_text()


def test_catalog_prepare_always_selects_unapproved_models(monkeypatch, tmp_path):
    forwarded = []
    monkeypatch.setattr(cli, "_watch_new_main", forwarded.append)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "catalog",
            "prepare",
            str(tmp_path / "watch.json"),
            "--against",
            str(tmp_path / "approved.json"),
            "--output",
            str(tmp_path / "candidates.json"),
        ],
    )

    cli.main()

    assert "--all-unapproved" in forwarded[0]


def test_catalog_test_writes_a_runnable_plan_for_approved_models(monkeypatch, tmp_path):
    watch = tmp_path / "watch.json"
    watch.write_text('{"prompt":"ok","discovery":[{"provider":"openai","limit":1}]}')
    approved = tmp_path / "approved.json"
    approved.write_text('{"models":[{"provider":"openai","model":"gpt-5.6-luna"}]}')
    output = tmp_path / "approved-tests.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "catalog",
            "test",
            str(watch),
            "--approved",
            str(approved),
            "--output",
            str(output),
        ],
    )

    cli.main()

    plan = json.loads(output.read_text())
    assert plan["prompt"] == "ok"
    assert plan["models"] == [{"provider": "openai", "model": "gpt-5.6-luna"}]
    assert plan["discovery"] == []


def test_catalog_refresh_lists_all_missing_provider_keys_before_discovery(
    monkeypatch, tmp_path, capsys
):
    watch = tmp_path / "watch.json"
    watch.write_text(
        json.dumps(
            {
                "prompt": "ok",
                "discovery": [
                    {"provider": "openai", "limit": 1},
                    {"provider": "anthropic", "limit": 1},
                ],
            }
        )
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        cli,
        "resolve_models",
        lambda config: (_ for _ in ()).throw(AssertionError("must not discover")),
    )
    monkeypatch.setattr(
        sys, "argv", ["llm-preflight", "catalog", "refresh", str(watch)]
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "OPENAI_API_KEY" in error
    assert "ANTHROPIC_API_KEY" in error
    assert str(tmp_path / ".env.production") in error


def test_catalog_candidate_selection_groups_before_listing_models():
    answers = iter(["1", "1"])
    output = []

    selected = cli.interactive_catalog_candidate_selection(
        [
            {
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "catalog_type": "text-ready",
            },
            {
                "provider": "openai",
                "model": "gpt-realtime-2",
                "catalog_type": "realtime",
            },
            {
                "provider": "anthropic",
                "model": "claude-sonnet-5",
                "catalog_type": "unknown",
            },
        ],
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
    )

    assert selected == [
        {"provider": "openai", "model": "gpt-5.6-luna", "catalog_type": "text-ready"}
    ]
    assert "=== Choose a provider ===" in output
    assert any("openai — 1 text-generation model" in line for line in output)
    assert "gpt-realtime-2" not in "\n".join(output)


def test_watch_new_writes_a_regular_candidate_benchmark_config(monkeypatch, tmp_path):
    watch = tmp_path / "watch.json"
    watch.write_text('{"prompt":"hello","models":[{"model":"old"}]}')
    approved = tmp_path / "approved.json"
    approved.write_text('{"models":[{"provider":"openai","model":"old"}]}')
    snapshot = tmp_path / "snapshot.json"
    output = tmp_path / "candidates.json"
    snapshot.write_text(
        json.dumps({"models": [{"provider": "openai", "model": "old"}]})
    )
    monkeypatch.setattr(
        cli,
        "resolve_models",
        lambda value: [
            {"provider": "openai", "model": "old"},
            {"provider": "openai", "model": "new"},
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "watch-new",
            str(watch),
            "--snapshot",
            str(snapshot),
            "--against",
            str(approved),
            "--write-config",
            str(output),
        ],
    )

    cli.main()

    assert json.loads(output.read_text())["models"] == [
        {"provider": "openai", "model": "new"},
    ]


def test_watch_new_candidate_config_does_not_persist_inherited_headers(
    monkeypatch, tmp_path
):
    watch = tmp_path / "watch.json"
    watch.write_text('{"prompt":"hello","models":[{"model":"old"}]}')
    approved = tmp_path / "approved.json"
    approved.write_text('{"models":[]}')
    output = tmp_path / "candidates.json"
    monkeypatch.setattr(
        cli,
        "resolve_models",
        lambda value: [
            {
                "provider": "openai",
                "model": "new",
                "headers": {"Authorization": "Bearer secret"},
            }
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "watch-new",
            str(watch),
            "--against",
            str(approved),
            "--all-unapproved",
            "--write-config",
            str(output),
        ],
    )

    cli.main()

    assert "secret" not in output.read_text()
    assert "headers" not in json.loads(output.read_text())["models"][0]


def test_watch_new_failed_candidate_run_does_not_advance_snapshot(
    monkeypatch, tmp_path
):
    watch = tmp_path / "watch.json"
    watch.write_text('{"prompt":"hello","models":[]}')
    approved = tmp_path / "approved.json"
    approved.write_text('{"models":[]}')
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps({"models": [{"provider": "openai", "model": "old"}]})
    )
    monkeypatch.setattr(
        cli, "resolve_models", lambda value: [{"provider": "openai", "model": "new"}]
    )
    monkeypatch.setattr(
        cli,
        "run_benchmark",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("failed")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "watch-new",
            str(watch),
            "--snapshot",
            str(snapshot),
            "--against",
            str(approved),
            "--test",
            "--no-save",
        ],
    )

    with pytest.raises(SystemExit):
        cli.main()

    assert json.loads(snapshot.read_text())["models"] == [
        {"provider": "openai", "model": "old"}
    ]


def test_watch_new_can_write_all_currently_unapproved_models(monkeypatch, tmp_path):
    watch = tmp_path / "watch.json"
    watch.write_text('{"prompt":"hello","models":[{"model":"old"}]}')
    approved = tmp_path / "approved.json"
    approved.write_text('{"models":[{"provider":"openai","model":"old"}]}')
    output = tmp_path / "candidates.json"
    monkeypatch.setattr(
        cli,
        "resolve_models",
        lambda value: [
            {"provider": "openai", "model": "old"},
            {"provider": "openai", "model": "already-discovered"},
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "watch-new",
            str(watch),
            "--against",
            str(approved),
            "--all-unapproved",
            "--write-config",
            str(output),
        ],
    )

    cli.main()

    assert json.loads(output.read_text())["models"] == [
        {"provider": "openai", "model": "already-discovered"}
    ]


def test_watch_new_reports_existing_output_as_a_cli_error(
    monkeypatch, tmp_path, capsys
):
    output = tmp_path / "candidates.json"
    output.write_text("{}")
    monkeypatch.setattr(
        cli,
        "_watch_new_main",
        lambda argv: (_ for _ in ()).throw(
            ValueError(f"{output} already exists; refusing to overwrite it")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["llm-preflight", "watch-new", "watch.json"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert f"llm-preflight: error: {output} already exists" in capsys.readouterr().err


def test_interactive_watch_reviews_unapproved_models_on_first_snapshot(
    monkeypatch, tmp_path, capsys
):
    config = tmp_path / "watch.json"
    config.write_text('{"prompt":"hello","models":[{"model":"old"}]}')
    approved = tmp_path / "approved.json"
    approved.write_text(
        json.dumps({"models": [{"provider": "openai", "model": "old"}]})
    )
    snapshot = tmp_path / "snapshot.json"
    monkeypatch.setattr(
        cli,
        "resolve_models",
        lambda value: [
            {"provider": "openai", "model": "old"},
            {"provider": "openai", "model": "new"},
        ],
    )
    selected = []
    monkeypatch.setattr(
        cli,
        "interactive_watch_selection",
        lambda watch, approved, candidates: (
            selected.append((watch, approved, candidates)) or None
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "watch-new",
            str(config),
            "--snapshot",
            str(snapshot),
            "--interactive",
            "--against",
            str(approved),
        ],
    )

    cli.main()

    assert selected == [
        (
            {"prompt": "hello", "models": [{"model": "old"}]},
            {"models": [{"provider": "openai", "model": "old"}]},
            [{"provider": "openai", "model": "new"}],
        )
    ]
    assert (
        "Catalog refreshed: 2 discovered; 1 approved; 1 available for review."
        in capsys.readouterr().out
    )


def test_watch_new_interrupt_exits_cleanly(monkeypatch, tmp_path, capsys):
    config = tmp_path / "watch.json"
    config.write_text('{"prompt":"hello","models":[{"model":"candidate"}]}')
    approved = tmp_path / "approved.json"
    approved.write_text('{"models": []}')
    monkeypatch.setattr(
        cli,
        "resolve_models",
        lambda value: [{"provider": "openai", "model": "candidate"}],
    )
    monkeypatch.setattr(
        cli,
        "interactive_watch_selection",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "watch-new",
            str(config),
            "--interactive",
            "--against",
            str(approved),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 130
    assert "Benchmark cancelled; no artifacts saved." in capsys.readouterr().err


def test_approve_model_records_a_passing_result(monkeypatch, tmp_path):
    result = tmp_path / "candidate.json"
    result.write_text(
        json.dumps(
            {
                "run_id": "run-123",
                "models": [
                    {
                        "provider": "openai",
                        "model": "candidate",
                        "name": "Candidate",
                        "summary": {"failed": 0},
                    }
                ],
            }
        )
    )
    approved = tmp_path / "approved.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "approve-model",
            "openai:candidate",
            "--from",
            str(result),
            "--approved",
            str(approved),
        ],
    )
    cli.main()
    saved = json.loads(approved.read_text())
    assert saved["models"] == [
        {"provider": "openai", "model": "candidate", "name": "Candidate"}
    ]
    assert saved["approvals"][0]["source_result"] == str(result)


def test_models_remove_deletes_a_permanent_model_and_records_when(
    monkeypatch, tmp_path
):
    approved = tmp_path / "approved.json"
    approved.write_text(
        json.dumps(
            {
                "models": [
                    {"provider": "openai", "model": "keep"},
                    {"provider": "openai", "model": "remove"},
                ],
                "approvals": [],
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "models",
            "remove",
            "openai:remove",
            "--approved",
            str(approved),
        ],
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    cli.main()

    saved = json.loads(approved.read_text())
    assert saved["models"] == [{"provider": "openai", "model": "keep"}]
    assert saved["removals"][0]["model"] == "remove"


def test_interactive_watch_selection_keeps_explicit_candidate_and_incumbent_choices():
    answers = iter(["2", "1", "2", "y"])
    output = []
    selected = cli.interactive_watch_selection(
        {"prompt": "hello"},
        {
            "models": [
                {"provider": "openai", "model": "incumbent-a"},
                {"provider": "openai", "model": "incumbent-b"},
            ]
        },
        [
            {"provider": "openai", "model": "candidate-a"},
            {"provider": "openai", "model": "candidate-b"},
        ],
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
    )

    assert selected is not None
    config, tests, smoke = selected
    assert config["models"] == [
        {"provider": "openai", "model": "incumbent-a"},
        {"provider": "openai", "model": "candidate-b"},
    ]
    assert tests == "exact-routing-check"
    assert smoke is True
    assert "=== Models to try ===" in output
    assert "=== Your current models ===" in output
    assert "These are not changed; they are only used for comparison." in output


def test_interactive_watch_all_excludes_the_paid_concurrency_profile():
    answers = iter(["1", "", "all", "y"])
    selected = cli.interactive_watch_selection(
        {"prompt": "hello"},
        {"models": []},
        [{"provider": "openai", "model": "candidate"}],
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _text: None,
    )

    assert selected is not None
    assert selected[1] == (
        "quick-migration-check,exact-routing-check,structured-output-check,"
        "numeric-instruction-check"
    )


def test_interactive_final_stage_keeps_head_to_head_models_and_requires_confirmation():
    answers = iter(["", "", "n"])
    selected = cli.interactive_selection(
        {"prompt": "hello", "models": [{"provider": "openai", "model": "old"}]},
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _text: None,
        selected_models=[
            {"provider": "openai", "model": "old"},
            {"provider": "openai", "model": "candidate"},
        ],
        selected_profile_selector="",
    )

    assert selected is None


def test_interactive_budget_requires_explicit_retry_risk_confirmation():
    output = []
    config = {
        "prompt": "hello",
        "models": [{"provider": "mock", "model": "candidate"}],
        "repetitions": 1,
        "warmups": 0,
        "max_requests": 1,
    }
    accepted = cli.confirm_interactive_budget(
        config,
        None,
        input_fn=lambda prompt: "ACCEPT 2",
        output_fn=output.append,
        color=True,
    )

    assert accepted is True
    assert "max_requests" not in config
    assert "This plan has 1 normal request and may make up to 2 with retries." in output
    assert "Your max_requests limit is 1." in "\n".join(output)
    assert output[0] == "\x1b[1;33m=== RETRY RISK ===\x1b[0m"


def test_interactive_budget_retries_after_an_invalid_acceptance_value():
    answers = iter(["ACCEPT 20", "ACCEPT 2"])
    output = []
    accepted = cli.confirm_interactive_budget(
        {
            "prompt": "hello",
            "models": [{"provider": "mock", "model": "candidate"}],
            "repetitions": 1,
            "warmups": 0,
            "max_requests": 1,
        },
        None,
        input_fn=lambda prompt: next(answers),
        output_fn=output.append,
    )

    assert accepted is True
    assert "That does not match ACCEPT 2. Try again or press Enter to cancel." in output


def test_main_runs_benchmark_saves_and_prints_console_report(
    monkeypatch, tmp_path, capsys
):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    result = {"models": [{"summary": {"failed": 0}}]}
    monkeypatch.setattr(cli, "run_benchmark", lambda *args, **kwargs: result)
    monkeypatch.setattr(cli, "save_result", lambda *args: tmp_path / "result.json")
    monkeypatch.setattr(cli, "console_report", lambda *args, **kwargs: "rendered")
    monkeypatch.setattr(sys, "argv", ["llm-preflight", str(config)])
    cli.main()
    captured = capsys.readouterr()
    assert captured.out.strip() == "rendered"
    assert "Saved raw result" in captured.err


def test_main_json_output_redacts_result_boundary(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    result = {
        "models": [{"summary": {"failed": 0}}],
        "source_config": {"api_token": "json-output-redaction-secret"},
    }
    saved = {}

    def fake_save(value, output_dir):
        saved.update(value)
        return tmp_path / "result.json"

    monkeypatch.setattr(cli, "run_benchmark", lambda *args, **kwargs: result)
    monkeypatch.setattr(cli, "save_result", fake_save)
    monkeypatch.setattr(sys, "argv", ["llm-preflight", str(config), "--json"])

    cli.main()

    assert "json-output-redaction-secret" not in capsys.readouterr().out
    assert saved["source_config"]["api_token"] == "[REDACTED]"


def test_main_smoke_defaults_to_failed_response_retention(monkeypatch, tmp_path):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    captured = {}
    result = {"models": [{"summary": {"failed": 0}}]}

    def fake_run(value, **kwargs):
        captured.update(value)
        return result

    monkeypatch.setattr(cli, "run_benchmark", fake_run)
    monkeypatch.setattr(cli, "save_result", lambda *args: tmp_path / "result.json")
    monkeypatch.setattr(cli, "console_report", lambda *args, **kwargs: "rendered")
    monkeypatch.setattr(sys, "argv", ["llm-preflight", str(config), "--smoke"])

    cli.main()

    assert captured["save_responses"] == "failures"


def test_main_dry_run_prints_resolved_plan_without_running(
    monkeypatch, tmp_path, capsys
):
    config = tmp_path / "benchmark.json"
    config.write_text(
        '{"prompt":"hello","models":[{"provider":"openai","model":"fake",'
        '"input_cost_per_million":1,"output_cost_per_million":2}],'
        '"presets":["low-latency"],"warmups":0}'
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("dry-run must not run benchmark")

    monkeypatch.setattr(cli, "run_benchmark", fail_run)
    monkeypatch.setattr(
        sys, "argv", ["llm-preflight", str(config), "--dry-run", "--json"]
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["models"][0]["provider"] == "openai"
    assert output["models"][0]["model"] == "fake"
    assert output["models"][0]["input_cost_per_million"] == 1
    assert "headers" not in output["models"][0]
    assert output["tests"] == ["config prompt"]
    assert output["test_breakdown"] == [
        {
            "name": "config prompt",
            "requests_per_model": 5,
            "details": "5 repetitions",
        }
    ]
    assert output["requests"] == 5
    assert output["possible_requests"] == 10
    assert output["estimated_cost_usd"] == 0.002565
    assert output["presets"] == ["low-latency"]
    assert output["request"]["max_output_tokens"] == 256
    assert output["stop_on"] == "none"
    assert output["pricing_warnings"] == []


def test_main_dry_run_prints_human_readable_plan_by_default(
    monkeypatch, tmp_path, capsys
):
    config = tmp_path / "benchmark.json"
    config.write_text(
        '{"name":"starter","prompt":"hello","models":[{"provider":"mock",'
        '"model":"local","response":"ok"}],"warmups":0}'
    )
    monkeypatch.setattr(sys, "argv", ["llm-preflight", str(config), "--dry-run"])

    cli.main()

    output = capsys.readouterr().out
    assert "=== RUN PLAN ===" in output
    assert "Benchmark: starter" in output
    assert "Models: mock:local" in output
    assert "Tests: config prompt" in output
    assert "Requests: 5 nominal; up to 10 with 2 attempts" in output
    assert "Cost: unavailable" in output
    assert "Stop on: none" in output


def test_dry_run_summarizes_large_model_and_pricing_lists():
    plan = {
        "benchmark": "catalog",
        "models": [
            {"provider": "openai", "model": f"gpt-{index}"} for index in range(12)
        ],
        "tests": ["config prompt"],
        "requests": 12,
        "possible_requests": 24,
        "retry_max_attempts": 2,
        "estimated_cost_usd": None,
        "maximum_estimated_cost_usd": None,
        "pricing_warnings": [
            {"model": f"gpt-{index}", "message": "pricing is unknown"}
            for index in range(12)
        ],
        "save_responses": False,
        "stop_on": "none",
    }

    output = cli._format_dry_run_plan(plan)

    assert "Models: 12 selected (openai: 12)" in output
    assert "gpt-11" not in output
    assert "Pricing warnings: 12 models have unknown or stale pricing." in output
    assert "... and 2 more" in output


def test_main_dry_run_includes_pricing_warnings(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text(
        '{"prompt":"hello","models":[{"provider":"openai_compatible",'
        '"model":"local"}],"warmups":0}'
    )
    monkeypatch.setattr(
        sys, "argv", ["llm-preflight", str(config), "--dry-run", "--json"]
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["pricing_warnings"][0]["message"] == "pricing is unknown"


def test_main_pricing_check_exits_nonzero_for_unknown_pricing(
    monkeypatch, tmp_path, capsys
):
    config = tmp_path / "benchmark.json"
    config.write_text(
        '{"prompt":"hello","models":[{"provider":"openai_compatible","model":"local"}]}'
    )
    monkeypatch.setattr(sys, "argv", ["llm-preflight", str(config), "--pricing-check"])

    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected pricing check to fail")

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["warnings"][0]["message"] == "pricing is unknown"


def test_main_dry_run_redacts_secrets(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text(
        '{"prompt":"hello","models":[{"provider":"openai_compatible",'
        '"model":"fake","base_url":"https://example.test",'
        '"headers":{"Authorization":"Bearer dry-run-redaction-secret"},'
        '"api_token":"dry-run-redaction-secret"}],'
        '"request":{"temperature":0,"metadata_token":"dry-run-redaction-secret"}}'
    )

    monkeypatch.setattr(
        sys, "argv", ["llm-preflight", str(config), "--dry-run", "--json"]
    )

    cli.main()

    output = capsys.readouterr().out
    assert "dry-run-redaction-secret" not in output
    parsed = json.loads(output)
    assert parsed["models"][0]["api_token"] == "[REDACTED]"
    assert parsed["request"]["metadata_token"] == "[REDACTED]"


def test_main_can_skip_default_env_file(monkeypatch, tmp_path):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    env_file = tmp_path / ".env.production"
    env_file.write_text("GEMINI_API_KEY=no-env-file-redaction-secret\n")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["llm-preflight", str(config), "--dry-run", "--no-env-file"]
    )

    cli.main()

    assert "GEMINI_API_KEY" not in __import__("os").environ


def test_main_can_load_explicit_env_file(monkeypatch, tmp_path):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    env_file = tmp_path / "custom.env"
    env_file.write_text("GEMINI_API_KEY=explicit-env-file-test-value\n")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-preflight", str(config), "--dry-run", "--env-file", str(env_file)],
    )

    cli.main()

    assert __import__("os").environ["GEMINI_API_KEY"] == "explicit-env-file-test-value"


def test_main_quick_loads_default_env_file_from_current_directory(
    monkeypatch, tmp_path
):
    env_file = tmp_path / ".env.production"
    env_file.write_text("GEMINI_API_KEY=quick-env-file-test-value\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "--quick",
            "hello",
            "--models",
            "openai:fake",
            "--dry-run",
        ],
    )

    cli.main()

    assert __import__("os").environ["GEMINI_API_KEY"] == "quick-env-file-test-value"


def test_main_quick_can_skip_default_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env.production"
    env_file.write_text("GEMINI_API_KEY=quick-no-env-file-test-value\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-preflight",
            "--quick",
            "hello",
            "--models",
            "openai:fake",
            "--dry-run",
            "--no-env-file",
        ],
    )

    cli.main()

    assert "GEMINI_API_KEY" not in __import__("os").environ


def test_main_stop_on_api_error_sets_config(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    captured = {}
    result = {"models": [{"summary": {"failed": 0}}]}

    def fake_run(value, **kwargs):
        captured.update(value)
        return result

    monkeypatch.setattr(cli, "run_benchmark", fake_run)
    monkeypatch.setattr(cli, "save_result", lambda *args: tmp_path / "result.json")
    monkeypatch.setattr(cli, "console_report", lambda *args, **kwargs: "rendered")
    monkeypatch.setattr(
        sys, "argv", ["llm-preflight", str(config), "--stop-on", "api-error"]
    )

    cli.main()

    assert captured["stop_on"] == "api-error"
    assert capsys.readouterr().out.strip() == "rendered"


def test_main_fail_fast_alias_sets_any_fail(monkeypatch, tmp_path):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    captured = {}
    result = {"models": [{"summary": {"failed": 0}}]}

    def fake_run(value, **kwargs):
        captured.update(value)
        return result

    monkeypatch.setattr(cli, "run_benchmark", fake_run)
    monkeypatch.setattr(cli, "save_result", lambda *args: tmp_path / "result.json")
    monkeypatch.setattr(cli, "console_report", lambda *args, **kwargs: "rendered")
    monkeypatch.setattr(sys, "argv", ["llm-preflight", str(config), "--fail-fast"])

    cli.main()

    assert captured["stop_on"] == "any-fail"


def test_main_dry_run_explains_all_tests_load_expansion(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text(
        '{"prompt":"hello","models":[{"provider":"openai","model":"fake"}],"warmups":0}'
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-preflight", str(config), "--tests", "all", "--dry-run", "--json"],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    load = next(
        item
        for item in output["test_breakdown"]
        if item["name"] == "concurrency-health-check"
    )
    assert load == {
        "name": "concurrency-health-check",
        "requests_per_model": 16,
        "details": "load levels: c1=1, c5=5, c10=10",
    }
    assert output["requests"] == 28


def test_main_enforces_budget_for_tests_selected_on_command_line(monkeypatch, tmp_path):
    config = tmp_path / "benchmark.json"
    config.write_text(
        '{"prompt":"hello","models":[{"model":"fake"}],"warmups":0,"max_requests":6}'
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("budget failure must prevent benchmark execution")

    monkeypatch.setattr(cli, "run_benchmark", fail_run)
    monkeypatch.setattr(sys, "argv", ["llm-preflight", str(config), "--tests", "all"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2


def test_main_exits_one_for_profile_validation_failures(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    result = {
        "models": [
            {
                "summary": {"failed": 0},
                "profiles": [
                    {
                        "name": "classification",
                        "summary": {
                            "failed": 0,
                            "valid_output_rate": 0,
                        },
                    }
                ],
            }
        ]
    }
    monkeypatch.setattr(cli, "run_benchmark", lambda *args, **kwargs: result)
    monkeypatch.setattr(cli, "save_result", lambda *args: tmp_path / "result.json")
    monkeypatch.setattr(cli, "console_report", lambda *args, **kwargs: "rendered")
    monkeypatch.setattr(sys, "argv", ["llm-preflight", str(config), "--tests", "all"])

    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected failed validation exit")

    assert capsys.readouterr().out.strip() == "rendered"


def test_main_no_save_skips_artifact_writes(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    result = {"models": [{"summary": {"failed": 0}}]}

    monkeypatch.setattr(cli, "run_benchmark", lambda *args, **kwargs: result)
    monkeypatch.setattr(
        cli,
        "save_result",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not save")),
    )
    monkeypatch.setattr(cli, "console_report", lambda *args, **kwargs: "rendered")
    monkeypatch.setattr(sys, "argv", ["llm-preflight", str(config), "--no-save"])

    cli.main()

    captured = capsys.readouterr()
    assert captured.out.strip() == "rendered"
    assert "Saved raw result" not in captured.err


def test_main_interrupt_exits_cleanly_without_saving(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_benchmark", interrupt)
    monkeypatch.setattr(
        cli,
        "save_result",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not save")),
    )
    monkeypatch.setattr(sys, "argv", ["llm-preflight", str(config)])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 130
    assert "Benchmark cancelled; no artifacts saved." in capsys.readouterr().err


def test_cli_process_exit_codes_stop_modes_and_budget_enforcement(tmp_path):
    def run(config: dict, *args: str) -> subprocess.CompletedProcess[str]:
        config_path = tmp_path / f"{len(list(tmp_path.iterdir()))}.json"
        config_path.write_text(json.dumps(config))
        environment = dict(os.environ)
        environment.pop("OPENAI_API_KEY", None)
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "llm_preflight.cli",
                str(config_path),
                "--no-save",
                "--json",
                *args,
            ],
            cwd=Path(__file__).parents[1],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    success = run(
        {
            "prompt": "hello",
            "models": [{"provider": "mock", "model": "local", "response": "ok"}],
            "warmups": 0,
        }
    )
    assert success.returncode == 0

    validation_failure = run(
        {
            "prompt": "hello",
            "validation": {"regex": "^ok$"},
            "models": [{"provider": "mock", "model": "local", "response": "no"}],
            "warmups": 0,
        }
    )
    assert validation_failure.returncode == 1

    config_failure = run({"prompt": "hello"})
    assert config_failure.returncode == 2

    test_stop = run(
        {
            "prompt": "hello",
            "validation": {"regex": "^ok$"},
            "models": [
                {"provider": "mock", "model": "first", "response": "no"},
                {"provider": "mock", "model": "second", "response": "ok"},
            ],
            "warmups": 0,
        },
        "--stop-on",
        "test-fail",
    )
    assert test_stop.returncode == 1
    assert len(json.loads(test_stop.stdout)["models"]) == 1

    api_stop = run(
        {
            "prompt": "hello",
            "models": [
                {"provider": "openai", "model": "first"},
                {"provider": "mock", "model": "second", "response": "ok"},
            ],
            "warmups": 0,
        },
        "--stop-on",
        "api-error",
    )
    assert api_stop.returncode == 1
    assert len(json.loads(api_stop.stdout)["models"]) == 1

    budget_failure = run(
        {
            "prompt": "hello",
            "models": [{"provider": "mock", "model": "local", "response": "ok"}],
            "warmups": 0,
            "max_requests": 1,
        }
    )
    assert budget_failure.returncode == 2
