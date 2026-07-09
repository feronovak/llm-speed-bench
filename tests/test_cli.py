import json
import sys

from llm_bench import cli
from llm_bench.cli import catalog_output, format_progress_event, interactive_selection


def test_interactive_selection_accepts_providers_families_profiles_and_repetitions(
    monkeypatch,
):
    models = [
        {"provider": "openai", "model": "gpt-5.5"},
        {"provider": "openai", "model": "gpt-5.4-mini"},
        {"provider": "openrouter", "model": "qwen/qwen3.7-plus"},
        {"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"},
    ]
    monkeypatch.setattr("llm_bench.cli.resolve_models", lambda config: models)
    answers = iter(["openai,openrouter/qwen", "1,4", "2", "y"])

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
    assert profiles == "chat-fast,reasoning"


def test_interactive_selection_can_cancel(monkeypatch):
    monkeypatch.setattr(
        "llm_bench.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    answers = iter(["all", "", "", "n"])

    selected = interactive_selection(
        {"prompt": "test", "models": [{"model": "gpt-5.5"}]},
        input_fn=lambda prompt: next(answers),
        output_fn=lambda text: None,
    )

    assert selected is None


def test_interactive_selection_lists_and_selects_named_custom_prompt(monkeypatch):
    monkeypatch.setattr(
        "llm_bench.cli.resolve_models",
        lambda config: [{"provider": "openai", "model": "gpt-5.5"}],
    )
    output = []
    answers = iter(["all", "csv-review", "", "y"])

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
    assert profiles is None
    assert config["prompt_name"] == "csv-review"
    assert config["prompt"] == "Review this CSV"
    assert any("csv-review" in line for line in output)
    assert any("long-summary" in line for line in output)


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
        sys, "argv", ["llm-bench", str(config), "--prompt", "csv-review"]
    )

    cli.main()

    assert captured_config["prompt_name"] == "csv-review"
    assert captured_config["prompt"] == "Review this CSV"
    assert capsys.readouterr().out.strip() == "rendered"


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
            "phase": "reasoning/reason-rate",
            "status": "error",
            "input_tokens": 12,
            "output_tokens": 0,
            "estimated_cost_usd": 0.000012,
            "error": "rate limited",
        }
    ) == (
        "  Request 1/3 [reasoning/reason-rate]: ERROR (rate limited) | "
        "tokens in/out 12/0 | cost $0.000012"
    )


def test_catalog_output_does_not_expose_custom_headers():
    output = catalog_output(
        [
            {
                "provider": "openai_compatible",
                "model": "private",
                "headers": {"Authorization": "Bearer secret", "X-API-Key": "secret"},
            }
        ]
    )
    assert "headers" not in output[0]
    assert "secret" not in str(output)


def test_main_catalog_prints_safe_json(monkeypatch, tmp_path, capsys):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    monkeypatch.setattr(
        cli,
        "resolve_models",
        lambda value: [{"model": "fake", "headers": {"Authorization": "secret"}}],
    )
    monkeypatch.setattr(sys, "argv", ["llm-bench", str(config), "--catalog"])
    cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output == [{"model": "fake"}]


def test_main_runs_benchmark_saves_and_prints_console_report(
    monkeypatch, tmp_path, capsys
):
    config = tmp_path / "benchmark.json"
    config.write_text('{"prompt":"hello","models":[{"model":"fake"}]}')
    result = {"models": [{"summary": {"failed": 0}}]}
    monkeypatch.setattr(cli, "run_benchmark", lambda *args, **kwargs: result)
    monkeypatch.setattr(cli, "save_result", lambda *args: tmp_path / "result.json")
    monkeypatch.setattr(cli, "console_report", lambda *args, **kwargs: "rendered")
    monkeypatch.setattr(sys, "argv", ["llm-bench", str(config)])
    cli.main()
    captured = capsys.readouterr()
    assert captured.out.strip() == "rendered"
    assert "Saved raw result" in captured.err
