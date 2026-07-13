from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .catalog import resolve_models
from .env import load_env_file
from .features import (
    apply_environment,
    apply_model_aliases,
    apply_provider_presets,
    apply_smoke_mode,
    check_budget,
    compare_results,
    doctor_report,
    estimate_budget,
    filter_changed_models,
    load_json,
    matrix_report,
    replay_config,
)
from .profiles import BUILTIN_PROFILES
from .pricing import pricing_freshness_report
from .redaction import redact_secrets
from .runner import (
    console_report,
    load_config,
    profile_request_breakdown,
    result_failed,
    run_benchmark,
    save_result,
    select_custom_prompt,
    select_test_profiles,
)


def catalog_output(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return catalog entries safe for terminal/CI output."""
    return redact_secrets(
        [
            {key: value for key, value in model.items() if key != "headers"}
            for model in models
        ]
    )


def format_progress_event(event: dict[str, Any], color: bool = False) -> str:
    if event["type"] == "model_start":
        return (
            f"{_style('Model', '1;36', color)} "
            f"{event['model_index']}/{event['model_total']}: "
            f"{event['provider']} — {event['model']} "
            f"({event['request_total']} requests)"
        )
    if event["type"] == "request_complete":
        if event["status"] != "ok":
            status_text = "API FAIL"
            if event.get("error"):
                status_text += f" ({event['error']})"
            status_color = "31"
        elif event.get("valid_output") is False:
            status_text = "API OK / TEST FAIL"
            if event.get("evaluation_error"):
                status_text += f" ({event['evaluation_error']})"
            status_color = "33"
        elif event.get("valid_output") is True:
            status_text = "API OK / TEST OK"
            status_color = "32"
        else:
            status_text = "API OK"
            status_color = "32"
        status = _style(status_text, status_color, color)
        input_tokens = event.get("input_tokens")
        output_tokens = event.get("output_tokens")
        tokens = (
            f"{input_tokens}/{output_tokens}"
            if input_tokens is not None and output_tokens is not None
            else "n/a"
        )
        cost = event.get("estimated_cost_usd")
        return (
            f"  Request {event['request_index']}/{event['request_total']} "
            f"[{event['phase']}]: {status} | tokens in/out {tokens} | "
            f"cost {'n/a' if cost is None else f'${cost:.6f}'}"
            + (
                f" | preview {event['response_preview']}"
                if event.get("response_preview")
                else ""
            )
        )
    if event["type"] == "model_complete":
        cost = event.get("estimated_cost_usd")
        invalid_outputs = int(event.get("invalid_outputs", 0))
        done_color = "1;32" if event["failed"] == 0 and invalid_outputs == 0 else "1;33"
        done = _style("Done:", done_color, color)
        return (
            f"  {done} {event['successful']}/{event['requests']} API ok, "
            f"{event['failed']} request errors, {invalid_outputs} invalid outputs | "
            f"tokens in/out "
            f"{event['input_tokens']}/{event['output_tokens']} | "
            f"cost {'n/a' if cost is None else f'${cost:.6f}'}"
        )
    raise ValueError(f"unknown progress event {event['type']!r}")


def _selected_numbers(value: str, count: int) -> list[int]:
    indexes = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit() or not 1 <= int(item) <= count:
            raise ValueError(
                f"invalid selection {item!r}; choose numbers from 1 to {count}"
            )
        indexes.append(int(item) - 1)
    return indexes


def _models_from_cli(value: str) -> list[dict[str, Any]]:
    models = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        provider, separator, model = item.partition(":")
        models.append(
            {"provider": provider, "model": model}
            if separator
            else {"provider": "openai", "model": provider}
        )
    if not models:
        raise ValueError("--models must name at least one model")
    return models


def _format_doctor(report: dict[str, Any]) -> str:
    lines = [
        "Doctor: " + ("ok" if report["ok"] else "failed"),
        f"Models: {report['models']}",
    ]
    for check in report["checks"]:
        status = "ok" if check["ok"] else "fail"
        model = f"{check.get('model')}: " if check.get("model") else ""
        lines.append(f"- {status}: {model}{check['message']}")
    return "\n".join(lines) + "\n"


def _format_diff(diff: dict[str, Any]) -> str:
    lines = [
        "| Model | Status | Latency p95 Δ | Success Δ | Cost Δ | Regressions |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in diff["models"]:
        latency = row.get("latency_p95_delta_seconds")
        success = row.get("success_rate_delta")
        cost = row.get("cost_delta_usd")
        lines.append(
            f"| {row['name']} | {row['status']} | "
            f"{'n/a' if latency is None else f'{latency:.3f}s'} | "
            f"{'n/a' if success is None else f'{success:+.0%}'} | "
            f"{'n/a' if cost is None else f'${cost:+.6f}'} | "
            f"{', '.join(row['regressions']) or 'none'} |"
        )
    return "\n".join(lines) + "\n"


def _selected_test_names(
    config: dict[str, Any], profile_selector: str | None
) -> list[str]:
    if profile_selector:
        return [
            profile["name"]
            for profile in select_test_profiles(config, profile_selector)
        ]
    if config.get("prompt_name"):
        return [config["prompt_name"]]
    return ["config prompt"]


def _test_breakdown(
    config: dict[str, Any], profile_selector: str | None
) -> list[dict[str, Any]]:
    if profile_selector:
        return profile_request_breakdown(
            select_test_profiles(config, profile_selector),
            int(config.get("suite_repetitions", 1)),
        )
    repetitions = int(config.get("repetitions", 5))
    return [
        {
            "name": config.get("prompt_name", "config prompt"),
            "requests_per_model": repetitions,
            "details": f"{repetitions} repetitions",
        }
    ]


def _dry_run_plan(
    config: dict[str, Any], profile_selector: str | None
) -> dict[str, Any]:
    budget_config = _budget_config(config, profile_selector)
    budget = estimate_budget(budget_config)
    models = resolve_models(config)
    return {
        "benchmark": config.get("name", "llm-benchmark"),
        "models": catalog_output(models),
        "tests": _selected_test_names(config, profile_selector),
        "test_breakdown": _test_breakdown(config, profile_selector),
        "requests": budget["requests"],
        "possible_requests": budget["possible_requests"],
        "retry_max_attempts": budget["retry_max_attempts"],
        "estimated_cost_usd": budget["estimated_cost_usd"],
        "maximum_estimated_cost_usd": budget["maximum_estimated_cost_usd"],
        "pricing_warnings": pricing_freshness_report(models)["warnings"],
        "presets": config.get("presets", []),
        "request": redact_secrets(
            config.get("request", {"temperature": 0, "max_output_tokens": 256})
        ),
        "save_responses": config.get("save_responses", False),
        "stop_on": config.get("stop_on", "none"),
    }


def _budget_config(
    config: dict[str, Any], profile_selector: str | None
) -> dict[str, Any]:
    budget_config = dict(config)
    if profile_selector:
        budget_config["profiles"] = profile_selector
    return budget_config


def _load_config_env_file(config_path: Path, args: argparse.Namespace) -> None:
    if args.no_env_file:
        return
    env_file = args.env_file or config_path.resolve().parent / ".env.production"
    load_env_file(env_file)


def _load_quick_env_file(args: argparse.Namespace) -> None:
    if args.no_env_file:
        return
    env_file = args.env_file or Path.cwd() / ".env.production"
    load_env_file(env_file)


def _style(text: str, code: str, color: bool) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if color else text


def _clear_screen() -> None:
    print("\x1b[2J\x1b[H", end="")


def interactive_selection(
    config: dict[str, Any],
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    color: bool = False,
    clear_fn: Callable[[], None] | None = None,
) -> tuple[dict[str, Any], str | None] | None:
    if clear_fn:
        clear_fn()
    models = resolve_models(config)
    if not models:
        raise ValueError("model discovery returned no models")

    output_fn(_style("Models", "1;36", color))
    for index, model in enumerate(models, 1):
        provider = model.get("provider", "openai_compatible")
        output_fn(
            f"  {_style(str(index) + '.', '1;33', color)} "
            f"{_style(provider, '36', color)}: {model['model']}"
        )
    model_answer = input_fn(
        "Select models (numbers, provider, provider/family, or all): "
    ).strip()
    if not model_answer or model_answer.casefold() == "all":
        selected_models = models
    else:
        selected_indexes: set[int] = set()
        selectors = [item.strip() for item in model_answer.split(",") if item.strip()]
        for selector in selectors:
            if selector.isdigit():
                selected_indexes.update(_selected_numbers(selector, len(models)))
                continue
            provider, separator, family = selector.casefold().partition("/")
            matches = {
                index
                for index, model in enumerate(models)
                if model.get("provider", "openai_compatible").casefold() == provider
                and (
                    not separator
                    or model["model"].casefold().startswith(family + "/")
                    or model["model"].casefold().startswith(family + "-")
                )
            }
            if not matches:
                raise ValueError(f"model selector {selector!r} matched no models")
            selected_indexes.update(matches)
        selected_models = [
            model for index, model in enumerate(models) if index in selected_indexes
        ]

    output_fn("")
    output_fn(_style("Tests", "1;35", color))
    tests: list[tuple[str, str, str]] = []
    for profile in BUILTIN_PROFILES:
        tests.append(("profile", profile["name"], profile["description"]))
    custom_prompts = config.get("prompts", [])
    for prompt in custom_prompts:
        tests.append(
            ("custom", prompt["name"], prompt.get("description", "Custom prompt test."))
        )
    for index, (_, name, description) in enumerate(tests, 1):
        output_fn(
            f"  {_style(str(index) + '.', '1;33', color)} "
            f"{_style(name, '1', color)} — {description}"
        )
    profile_answer = input_fn(
        "Select tests (numbers/names/all) or Enter for the config prompt: "
    ).strip()
    if profile_answer.casefold() == "all":
        profile_selector = "all"
    elif profile_answer:
        selected_names: list[str] = []
        for item in [
            part.strip() for part in profile_answer.split(",") if part.strip()
        ]:
            if item.isdigit():
                selected_names.extend(
                    tests[index][1] for index in _selected_numbers(item, len(tests))
                )
            else:
                selected_names.append(item)
        profile_selector = ",".join(selected_names)
    else:
        if "prompt" not in config:
            raise ValueError("select a custom prompt name or built-in profile")
        profile_selector = None

    default_repetitions = int(
        config.get(
            "suite_repetitions" if profile_selector else "repetitions",
            1 if profile_selector else 5,
        )
    )
    output_fn("")
    output_fn(_style("Repetitions", "1;34", color))
    repetitions_answer = input_fn(
        f"Repetitions per test [{default_repetitions}]: "
    ).strip()
    repetitions = int(repetitions_answer) if repetitions_answer else default_repetitions
    if repetitions < 1:
        raise ValueError("repetitions must be positive")

    output_fn("")
    output_fn(_style("Stop Mode", "1;31", color))
    output_fn("  1. any-fail — stop after API error or TEST FAIL")
    output_fn("  2. api-error — stop only if the provider/request breaks")
    output_fn("  3. test-fail — stop on model output failure, ignore API-ok passes")
    output_fn("  4. never — run every selected model")
    stop_answer = input_fn("Stop on [1]: ").strip().casefold()
    confirmation_answer: str | None = None
    if stop_answer in {"", "1", "any-fail"}:
        stop_on = "any-fail"
    elif stop_answer in {"2", "api-error"}:
        stop_on = "api-error"
    elif stop_answer in {"3", "test-fail"}:
        stop_on = "test-fail"
    elif stop_answer in {"4", "never", "none"}:
        stop_on = None
    elif stop_answer in {"y", "yes", "n", "no"}:
        stop_on = "any-fail"
        confirmation_answer = stop_answer
    else:
        raise ValueError(
            "stop mode must be 1, 2, 3, 4, any-fail, api-error, test-fail, or never"
        )

    selected_config = dict(config)
    selected_config["models"] = selected_models
    selected_config["discovery"] = []
    selected_config["repetitions"] = repetitions
    selected_config["suite_repetitions"] = repetitions
    if profile_selector:
        selected_config["profiles"] = profile_selector
    if stop_on:
        selected_config["stop_on"] = stop_on
    selected_config.setdefault("save_responses", "failures")
    budget = estimate_budget(selected_config)
    estimated_cost = budget["estimated_cost_usd"]
    maximum_cost = budget["maximum_estimated_cost_usd"]
    profile_label = profile_selector or selected_config.get(
        "prompt_name", "config prompt"
    )
    output_fn("")
    output_fn(_style("Run plan", "1;36", color))
    output_fn(f"  {_style('Models:', '1;36', color)} {len(selected_models)} selected")
    output_fn(f"  {_style('Tests:', '1;35', color)} {profile_label}")
    output_fn(f"  {_style('Repetitions:', '1;34', color)} {repetitions}")
    output_fn(f"  {_style('Stop on:', '1;31', color)} {stop_on or 'never'}")
    output_fn(
        f"  {_style('Estimate:', '1;33', color)} "
        f"{budget['requests']} nominal requests, up to "
        f"{budget['possible_requests']} with retries; "
        f"{'cost unavailable' if estimated_cost is None else f'${estimated_cost:.6f} nominal'}"
        f"{' ' if maximum_cost is None else f' (up to ${maximum_cost:.6f} with retries)'}."
    )
    output_fn(
        "  "
        + _style("Status:", "1;36", color)
        + " API OK means the provider returned a response; "
        + "TEST OK/FAIL means the evaluator accepted or rejected it."
    )
    output_fn(
        f"  {_style('Saved output:', '1;36', color)} "
        f"{selected_config['save_responses']}"
    )
    if profile_selector:
        output_fn("Request breakdown per model:")
        for breakdown_item in _test_breakdown(selected_config, profile_selector):
            output_fn(
                f"  {breakdown_item['name']}: "
                f"{breakdown_item['requests_per_model']} ({breakdown_item['details']})"
            )
    if confirmation_answer is None:
        confirmation_answer = input_fn("Run paid benchmark? [y/N]: ").strip().casefold()
    if confirmation_answer not in {"y", "yes"}:
        output_fn(_style("Cancelled.", "1;33", color))
        return None
    return selected_config, profile_selector


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark configurable LLM providers")
    parser.add_argument(
        "config", type=Path, nargs="?", help="benchmark JSON configuration"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="print results without writing result artifacts",
    )
    parser.add_argument("--json", action="store_true", help="print full JSON result")
    parser.add_argument(
        "--env", dest="environment_name", help="apply a named config environment"
    )
    parser.add_argument(
        "--smoke", action="store_true", help="run one request per model with no warmup"
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="validate config, keys, and model resolution",
    )
    parser.add_argument(
        "--pricing-check",
        action="store_true",
        help="check pricing freshness and unknown prices without benchmarking",
    )
    parser.add_argument(
        "--baseline", type=Path, help="compare this run with a previous result"
    )
    parser.add_argument(
        "--ci", action="store_true", help="fail when baseline thresholds regress"
    )
    parser.add_argument(
        "--matrix", action="store_true", help="print model-by-profile quality matrix"
    )
    parser.add_argument("--quick", help="run an ad hoc prompt without a config file")
    parser.add_argument(
        "--models", help="comma-separated provider:model list for --quick"
    )
    parser.add_argument("--diff", nargs=2, type=Path, metavar=("BASELINE", "CURRENT"))
    parser.add_argument(
        "--replay", type=Path, help="re-run the exact saved source config"
    )
    parser.add_argument(
        "--changed-since",
        type=Path,
        help="run only models not present in a catalog JSON",
    )
    parser.add_argument(
        "--catalog",
        action="store_true",
        help="discover and print selected models without benchmarking them",
    )
    parser.add_argument(
        "--tests",
        dest="tests",
        help="alias for --profiles; comma-separated built-in or custom tests",
    )
    parser.add_argument(
        "--profiles",
        help="compatibility alias for --tests",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print resolved models, tests, request count, and cost without running",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="do not load .env.production next to the config",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="load environment variables from this file instead of .env.production",
    )
    parser.add_argument(
        "--stop-on",
        choices=("api-error", "test-fail", "any-fail"),
        help="stop after the first model with this failure type",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="compatibility alias for --stop-on any-fail",
    )
    parser.add_argument(
        "--prompt",
        dest="prompt_name",
        help="run one named custom prompt from the config",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="interactively select models, tests, and repetitions",
    )
    args = parser.parse_args()
    path: Path | None = None
    try:
        if args.no_env_file and args.env_file:
            parser.error("--no-env-file cannot be combined with --env-file")
        if args.diff:
            diff = compare_results(load_json(args.diff[0]), load_json(args.diff[1]))
            print(json.dumps(diff, indent=2) if args.json else _format_diff(diff))
            if args.ci and not diff["ok"]:
                raise SystemExit(1)
            return
        if args.quick:
            if not args.models:
                parser.error("--quick requires --models")
            _load_quick_env_file(args)
            config = {
                "name": "quick",
                "prompt": args.quick,
                "models": _models_from_cli(args.models),
                "repetitions": 1,
                "warmups": 0,
            }
        elif args.replay:
            config = replay_config(load_json(args.replay))
            _load_config_env_file(args.replay, args)
        else:
            if args.config is None:
                parser.error(
                    "config is required unless --quick, --diff, or --replay is used"
                )
            _load_config_env_file(args.config, args)
            config = load_config(args.config)
        config = apply_environment(config, args.environment_name)
        config = apply_model_aliases(config)
        config = apply_provider_presets(config)
        if args.profiles and args.tests:
            parser.error("--profiles cannot be combined with --tests")
        if args.profiles and args.prompt_name:
            parser.error("--profiles cannot be combined with --prompt")
        if args.tests and args.prompt_name:
            parser.error("--tests cannot be combined with --prompt")
        if args.interactive and (
            args.catalog or args.profiles or args.tests or args.prompt_name
        ):
            parser.error(
                "--interactive cannot be combined with --catalog, --profiles, "
                "--tests, or --prompt"
            )
        if args.smoke:
            config = apply_smoke_mode(config)
            config.setdefault("save_responses", "failures")
        if args.stop_on:
            config["stop_on"] = args.stop_on
        if args.fail_fast:
            config["stop_on"] = "any-fail"
        if args.doctor:
            report_data = doctor_report(config)
            print(
                json.dumps(report_data, indent=2)
                if args.json
                else _format_doctor(report_data)
            )
            if not report_data["ok"]:
                raise SystemExit(1)
            return
        if args.pricing_check:
            report_data = pricing_freshness_report(resolve_models(config))
            print(json.dumps(report_data, indent=2))
            if not report_data["ok"]:
                raise SystemExit(1)
            return
        if args.changed_since:
            previous = load_json(args.changed_since)
            previous_models = previous.get("models", previous)
            config["models"] = filter_changed_models(
                resolve_models(config), previous_models
            )
            config["discovery"] = []
        if args.catalog:
            print(json.dumps(catalog_output(resolve_models(config)), indent=2))
            return
        profile_selector = args.tests or args.profiles
        if args.prompt_name:
            config = select_custom_prompt(config, args.prompt_name)
        if args.interactive:
            selection = interactive_selection(
                config,
                color=sys.stdout.isatty(),
                clear_fn=_clear_screen,
            )
            if selection is None:
                return
            config, profile_selector = selection
        if args.dry_run:
            print(json.dumps(_dry_run_plan(config, profile_selector), indent=2))
            return
        check_budget(_budget_config(config, profile_selector))
        use_color = sys.stdout.isatty()
        result = run_benchmark(
            config,
            profile_selector=profile_selector,
            progress=(
                (
                    lambda event: print(
                        format_progress_event(event, color=use_color), flush=True
                    )
                )
                if args.interactive
                else None
            ),
        )
        result = redact_secrets(result)
        if not args.no_save:
            path = save_result(result, args.output_dir)
    except KeyboardInterrupt:
        print("Benchmark cancelled; no artifacts saved.", file=sys.stderr)
        raise SystemExit(130) from None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(redact_secrets(str(exc))))
    print(
        json.dumps(result, indent=2)
        if args.json
        else matrix_report(result)
        if args.matrix
        else console_report(result, color=sys.stdout.isatty())
    )
    if args.baseline:
        diff = compare_results(load_json(args.baseline), result)
        print(_format_diff(diff))
        if args.ci and not diff["ok"]:
            raise SystemExit(1)
    if path is not None:
        print(f"Saved raw result to {path}", file=sys.stderr)
    if result_failed(result):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
