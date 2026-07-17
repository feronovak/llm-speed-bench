from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .catalog import classify_catalog_model, resolve_models
from .client import PROVIDER_DEFAULTS
from .catalog_watch import (
    build_candidate_config,
    catalog_diff,
    default_snapshot_path,
    load_snapshot,
    save_snapshot,
    snapshot_catalog,
)
from .catalog_probe import probe_model
from .capability_ledger import apply_probe_evidence, load_ledger
from .env import load_env_file
from .features import (
    apply_environment,
    apply_migration_check,
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
    benchmark_run_lock,
    console_report,
    load_config,
    model_failed,
    profile_request_breakdown,
    result_failed,
    validate_config_validations,
    run_benchmark,
    save_result,
    select_custom_prompt,
    select_test_profiles,
)


_ENV_TEMPLATE = """# Copy this file to .env.production beside your benchmark configuration.
# Fill only the providers you use. Never commit .env.production.

OPENAI_API_KEY=""
ANTHROPIC_API_KEY=""
GEMINI_API_KEY=""
OPENROUTER_API_KEY=""
XAI_API_KEY=""
"""


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


def _format_dry_run_plan(plan: dict[str, Any]) -> str:
    if len(plan["models"]) <= 10:
        models = ", ".join(
            f"{model.get('provider', 'openai_compatible')}:{model['model']}"
            for model in plan["models"]
        )
    else:
        providers = Counter(
            model.get("provider", "openai_compatible") for model in plan["models"]
        )
        summary = ", ".join(
            f"{provider}: {count}" for provider, count in providers.items()
        )
        models = f"{len(plan['models'])} selected ({summary})"
    estimated_cost = plan["estimated_cost_usd"]
    maximum_cost = plan["maximum_estimated_cost_usd"]
    cost = (
        "unavailable"
        if estimated_cost is None
        else f"${estimated_cost:.6f} nominal"
        + ("" if maximum_cost is None else f"; up to ${maximum_cost:.6f} with retries")
    )
    lines = [
        "=== RUN PLAN ===",
        f"Benchmark: {plan['benchmark']}",
        f"Models: {models}",
        f"Tests: {', '.join(plan['tests'])}",
        (
            f"Requests: {plan['requests']} nominal; up to "
            f"{plan['possible_requests']} with {plan['retry_max_attempts']} attempts"
        ),
        f"Cost: {cost}",
        f"Stop on: {plan['stop_on']}",
        f"Saved responses: {plan['save_responses']}",
    ]
    if plan["pricing_warnings"]:
        warning_count = len(plan["pricing_warnings"])
        lines.append(
            f"Pricing warnings: {warning_count} models have unknown or stale pricing."
        )
        lines.extend(
            f"- {warning['model']}: {warning['message']}"
            for warning in plan["pricing_warnings"][:10]
        )
        if warning_count > 10:
            lines.append(f"- ... and {warning_count - 10} more (use --json for all)")
    return "\n".join(lines) + "\n"


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


def _starter_config() -> dict[str, Any]:
    return {
        "name": "first-run",
        "prompt": "Reply with ok.",
        "validation": {"exact": "ok"},
        "models": [
            {
                "name": "local-mock",
                "provider": "mock",
                "model": "local",
                "response": "ok",
            }
        ],
        "repetitions": 1,
        "warmups": 0,
        "save_responses": False,
    }


def _write_starter_config(path: Path) -> None:
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(_starter_config(), handle, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise ValueError(f"{path} already exists; refusing to overwrite it") from exc


def _display_command() -> str:
    if Path(sys.argv[0]).name in {"cli.py", "__main__.py"}:
        return "python3 -m llm_preflight"
    return "llm-preflight"


def _format_catalog_watch(payload: dict[str, Any]) -> str:
    if payload["initialized"]:
        categories = ", ".join(
            f"{kind}: {count}" for kind, count in payload["categories"].items()
        )
        return (
            f"Catalog snapshot initialized: {payload['snapshot']}\n"
            f"Tracked models: {payload['models']}\n"
            f"Categories: {categories}\n"
            "Run this command again after providers update their catalogs.\n"
        )
    lines = [f"Catalog updated: {payload['snapshot']}"]
    lines.append(
        "Categories: "
        + ", ".join(f"{kind}: {count}" for kind, count in payload["categories"].items())
    )
    for label in ("added", "removed", "renamed", "changed"):
        items = payload["diff"][label]
        lines.append(f"{label.title()}: {len(items)}")
        for item in items:
            model = item.get("model", "")
            provider = item.get("provider", "")
            detail = f" ({', '.join(item['fields'])})" if item.get("fields") else ""
            lines.append(f"  {provider}:{model}{detail}")
    return "\n".join(lines) + "\n"


def _select_numbered_models(
    models: list[dict[str, Any]], answer: str, default_all: bool
) -> list[dict[str, Any]]:
    answer = answer.strip().casefold()
    if not answer:
        return list(models) if default_all else []
    if answer == "all":
        return list(models)
    indexes = set()
    for item in [part.strip() for part in answer.split(",") if part.strip()]:
        if item.isdigit():
            indexes.update(_selected_numbers(item, len(models)))
            continue
        provider, separator, family = item.partition("/")
        provider_matches = {
            index
            for index, model in enumerate(models)
            if model.get("provider", "openai_compatible").casefold()
            == provider.casefold()
        }
        exact_matches = {
            index
            for index in provider_matches
            if separator and models[index]["model"].casefold() == family.casefold()
        }
        matches = exact_matches or {
            index
            for index in provider_matches
            if not separator
            or models[index]["model"].casefold().startswith(family.casefold() + "/")
            or models[index]["model"].casefold().startswith(family.casefold() + "-")
        }
        if not matches:
            raise ValueError(
                "select model numbers, a provider, provider/family, or all"
            )
        indexes.update(matches)
    return [model for index, model in enumerate(models) if index in indexes]


def interactive_catalog_candidate_selection(
    candidates: list[dict[str, Any]],
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Choose a small text-generation candidate set in two readable stages."""
    eligible = [
        model
        for model in candidates
        if classify_catalog_model(model)["catalog_type"] in {"text-ready", "text-chat"}
    ]
    if not eligible:
        output_fn("No text-generation candidates are available.")
        return []
    providers = list(
        dict.fromkeys(model.get("provider", "openai_compatible") for model in eligible)
    )
    output_fn("=== Choose a provider ===")
    output_fn(
        "Only text-ready models are shown; candidates need a separate one-request probe."
    )
    for index, provider in enumerate(providers, 1):
        count = sum(
            model.get("provider", "openai_compatible") == provider for model in eligible
        )
        label = "model" if count == 1 else "models"
        output_fn(f"  {index}. {provider} — {count} text-generation {label}")
    provider_answer = input_fn("Choose provider numbers/names, or Enter to cancel: ")
    selected_providers = _select_numbered_models(
        [{"provider": provider, "model": provider} for provider in providers],
        provider_answer,
        default_all=False,
    )
    if not selected_providers:
        output_fn("Cancelled.")
        return []
    provider_names = {item["provider"] for item in selected_providers}
    visible = [
        model
        for model in eligible
        if model.get("provider", "openai_compatible") in provider_names
    ]
    output_fn("")
    output_fn("=== Choose models to test ===")
    for index, model in enumerate(visible, 1):
        output_fn(f"  {index}. {model['provider']}:{model['model']}")
    return _select_numbered_models(
        visible,
        input_fn("Choose model numbers, provider/family, or Enter to cancel: "),
        default_all=False,
    )


def interactive_watch_selection(
    watch_config: dict[str, Any],
    approved_config: dict[str, Any],
    candidates: list[dict[str, Any]],
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> tuple[dict[str, Any], str | None, bool] | None:
    """Choose models to try and compare without modifying approval state."""
    output_fn("=== Models to try ===")
    for index, model in enumerate(candidates, 1):
        output_fn(
            f"  {index}. {model.get('provider', 'openai_compatible')}:{model['model']}"
        )
    selected_candidates = _select_numbered_models(
        candidates,
        input_fn(
            "Choose models to try (numbers, provider, provider/family, or all) "
            "or Enter to cancel: "
        ),
        default_all=False,
    )
    if not selected_candidates:
        output_fn("Cancelled.")
        return None

    incumbents = approved_config.get("models", [])
    output_fn("")
    output_fn("=== Your current models ===")
    output_fn("These are not changed; they are only used for comparison.")
    for index, model in enumerate(incumbents, 1):
        output_fn(
            f"  {index}. {model.get('provider', 'openai_compatible')}:{model['model']}"
        )
    selected_incumbents = _select_numbered_models(
        incumbents,
        input_fn(
            "Choose models to compare against "
            "(numbers, provider, provider/family, or all) [all]: "
        ),
        default_all=True,
    )

    output_fn("")
    output_fn("=== Tests ===")
    tests = [(profile["name"], profile["description"]) for profile in BUILTIN_PROFILES]
    tests.extend(
        (prompt["name"], prompt.get("description", "Custom prompt test."))
        for prompt in watch_config.get("prompts", [])
    )
    for index, (name, description) in enumerate(tests, 1):
        output_fn(f"  {index}. {name} — {description}")
    answer = input_fn(
        "Select tests (numbers/names/all) or Enter for the config prompt: "
    ).strip()
    if answer.casefold() == "all":
        selected_tests = ",".join(
            profile["name"]
            for profile in BUILTIN_PROFILES
            if "concurrency_levels" not in profile
        )
    elif answer:
        selected_names: list[str] = []
        for item in [part.strip() for part in answer.split(",") if part.strip()]:
            if item.isdigit():
                selected_names.extend(
                    tests[index][0] for index in _selected_numbers(item, len(tests))
                )
            else:
                selected_names.append(item)
        selected_tests = ",".join(selected_names)
    else:
        selected_tests = None
        if "prompt" not in watch_config:
            raise ValueError("select a test or configure a top-level prompt")
    smoke = input_fn("Use smoke mode? [Y/n]: ").strip().casefold() not in {"n", "no"}
    selected_approved = {"models": selected_incumbents}
    config = build_candidate_config(
        watch_config, selected_approved, selected_candidates
    )
    return config, selected_tests, smoke


def confirm_interactive_budget(
    config: dict[str, Any],
    profile_selector: str | None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    color: bool = False,
) -> bool:
    """Require explicit consent before exceeding only the retry-safe request cap."""
    budget_config = _budget_config(config, profile_selector)
    try:
        check_budget(budget_config)
        return True
    except ValueError as exc:
        budget = estimate_budget(budget_config)
        max_requests = budget_config.get("max_requests")
        request_limit_exceeded = max_requests is not None and budget[
            "possible_requests"
        ] > int(max_requests)
        if not request_limit_exceeded:
            output_fn(f"Cannot run this plan: {exc}")
            return False

        without_request_limit = dict(budget_config)
        without_request_limit.pop("max_requests", None)
        try:
            check_budget(without_request_limit)
        except ValueError as remaining_error:
            output_fn(f"Cannot run this plan: {remaining_error}")
            return False

        output_fn(_style("=== RETRY RISK ===", "1;33", color))
        output_fn(
            f"This plan has {budget['requests']} normal request"
            f"{'s' if budget['requests'] != 1 else ''} and may make up to "
            f"{budget['possible_requests']} with retries."
        )
        output_fn(_style(f"Your max_requests limit is {max_requests}.", "1;31", color))
        required = f"ACCEPT {budget['possible_requests']}"
        while True:
            answer = input_fn(
                "Type "
                + _style(required, "1;33", color)
                + " to accept the retry risk, or Enter to cancel: "
            ).strip()
            if answer == required:
                break
            if not answer:
                output_fn("Cancelled.")
                return False
            output_fn(
                f"That does not match {required}. Try again or press Enter to cancel."
            )
        config.pop("max_requests", None)
        return True


def _missing_discovery_keys(config: dict[str, Any]) -> list[str]:
    """Return every configured discovery credential that is not available."""
    missing = []
    for source in config.get("discovery", []):
        provider = source.get("provider", "openai_compatible")
        key_env = source.get(
            "api_key_env", PROVIDER_DEFAULTS.get(provider, {}).get("api_key_env")
        )
        if key_env and not os.environ.get(key_env) and key_env not in missing:
            missing.append(key_env)
    return missing


def _create_catalog_env_file(env_path: Path) -> None:
    """Reuse a project-level env file instead of copying credentials."""
    shared_env = env_path.parent.parent / ".env.production"
    if env_path.is_symlink() and not env_path.exists():
        env_path.unlink()
    if shared_env.is_file():
        env_path.symlink_to(os.path.relpath(shared_env, env_path.parent))
        return
    env_path.write_text(_ENV_TEMPLATE, encoding="utf-8")


def _watch_new_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        description="Watch provider catalogs for new models"
    )
    parser.add_argument("config", type=Path, help="watch benchmark JSON configuration")
    parser.add_argument("--snapshot", type=Path, help="local catalog snapshot path")
    parser.add_argument(
        "--write-config",
        type=Path,
        help="write a regular benchmark config for newly discovered models",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="replace an existing candidate config (only with --write-config)",
    )
    parser.add_argument(
        "--all-unapproved",
        action="store_true",
        help="with --write-config, include all currently unapproved models",
    )
    parser.add_argument(
        "--select-candidates",
        action="store_true",
        help="choose a small text-generation candidate set before writing config",
    )
    parser.add_argument("--test", action="store_true", help="compare new candidates")
    parser.add_argument(
        "--against", type=Path, help="approved benchmark JSON configuration"
    )
    parser.add_argument("--tests", help="comma-separated built-in or custom tests")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--no-env-file", action="store_true")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    if args.no_env_file and args.env_file:
        parser.error("--no-env-file cannot be combined with --env-file")
    if (args.test or args.interactive or args.write_config) and not args.against:
        parser.error("--test, --interactive, and --write-config require --against")

    _load_config_env_file(args.config, args)
    watch_config = apply_provider_presets(apply_model_aliases(load_config(args.config)))
    missing_keys = _missing_discovery_keys(watch_config)
    if missing_keys:
        keys = ", ".join(missing_keys)
        raise ValueError(
            f"catalog refresh needs these API keys: {keys}. Add them to "
            f"{args.config.parent / '.env.production'} and run refresh again"
        )
    ledger_path = args.config.parent / ".llm-preflight" / "capabilities.json"
    models = apply_probe_evidence(
        resolve_models(watch_config), load_ledger(ledger_path)
    )
    snapshot_path = args.snapshot or default_snapshot_path(args.config)
    previous = load_snapshot(snapshot_path)
    current = snapshot_catalog(models)
    diff = catalog_diff(previous, current) if previous is not None else None
    payload = {
        "snapshot": str(snapshot_path),
        "models": len(current),
        "categories": dict(
            sorted(
                Counter(
                    model.get("catalog_type", "unknown") for model in current
                ).items()
            )
        ),
        "initialized": previous is None,
        "diff": diff,
    }
    if not args.test and not args.interactive and not args.write_config:
        save_snapshot(snapshot_path, models)
        print(
            json.dumps(payload, indent=2)
            if args.json
            else _format_catalog_watch(payload)
        )
        return
    approved_config = load_json(args.against)
    approved_models = approved_config.get("models", [])
    if not isinstance(approved_models, list) or any(
        not isinstance(model, dict) or not isinstance(model.get("model"), str)
        for model in approved_models
    ):
        raise ValueError("approved configuration requires models as objects with model")
    approved_config["models"] = approved_models
    if args.write_config:
        if args.all_unapproved:
            approved_keys = {
                (item.get("provider", "openai_compatible"), item["model"])
                for item in approved_config.get("models", [])
            }
            candidates = [
                model
                for model in models
                if (model.get("provider", "openai_compatible"), model["model"])
                not in approved_keys
            ]
        else:
            candidate_keys = {
                (item.get("provider", "openai_compatible"), item["model"])
                for item in (diff["added"] if diff is not None else [])
            }
            candidates = [
                model
                for model in models
                if (model.get("provider", "openai_compatible"), model["model"])
                in candidate_keys
            ]
        if not candidates:
            print("No newly discovered models to write.", file=sys.stderr)
            return
        if args.select_candidates:
            candidates = interactive_catalog_candidate_selection(candidates)
            if not candidates:
                return
        if args.write_config.exists() and not args.replace:
            raise ValueError(
                f"{args.write_config} already exists; use --replace to update it"
            )
        config = dict(watch_config)
        config["models"] = catalog_output(candidates)
        config["discovery"] = []
        args.write_config.write_text(
            json.dumps(redact_secrets(config), indent=2) + "\n", encoding="utf-8"
        )
        print(f"Wrote candidate benchmark config to {args.write_config}")
        return
    if args.interactive:
        approved_keys = {
            (item.get("provider", "openai_compatible"), item["model"])
            for item in approved_config.get("models", [])
        }
        candidates = [
            model
            for model in models
            if (model.get("provider", "openai_compatible"), model["model"])
            not in approved_keys
        ]
        print(
            f"Catalog refreshed: {len(models)} discovered; "
            f"{len(approved_keys)} approved; {len(candidates)} available for review."
        )
        if not candidates:
            print("Every discovered model is already approved.")
            return
        selection = interactive_watch_selection(
            watch_config,
            approved_config,
            candidates,
        )
        if selection is None:
            return
        config, args.tests, smoke = selection
        if smoke:
            config = apply_smoke_mode(config)
            config.setdefault("save_responses", "failures")
        final_selection = interactive_selection(
            config,
            color=sys.stdout.isatty(),
            clear_fn=_clear_screen,
            selected_models=config["models"],
            selected_profile_selector=args.tests if args.tests is not None else "",
        )
        if final_selection is None:
            return
        config, args.tests = final_selection
    else:
        candidate_keys = {
            (item.get("provider", "openai_compatible"), item["model"])
            for item in (diff["added"] if diff is not None else models)
        }
        candidates = [
            model
            for model in models
            if (model.get("provider", "openai_compatible"), model["model"])
            in candidate_keys
        ]
        if not candidates:
            print("No new catalog candidates to test.", file=sys.stderr)
            return
        config = build_candidate_config(watch_config, approved_config, candidates)
    if args.smoke:
        config = apply_smoke_mode(config)
        config.setdefault("save_responses", "failures")
    if args.dry_run:
        plan = _dry_run_plan(config, args.tests)
        plan["catalog_candidates"] = catalog_output(candidates)
        print(json.dumps(plan, indent=2) if args.json else _format_dry_run_plan(plan))
        return
    if not args.interactive:
        check_budget(_budget_config(config, args.tests))
    with benchmark_run_lock(args.output_dir):
        result = redact_secrets(run_benchmark(config, profile_selector=args.tests))
    path = None if args.no_save else save_result(result, args.output_dir)
    print(json.dumps(result, indent=2) if args.json else console_report(result))
    if path is not None:
        print(f"Saved raw result to {path}", file=sys.stderr)
    save_snapshot(snapshot_path, models)
    if args.interactive and path is not None:
        candidate_keys = {
            (model.get("provider", "openai_compatible"), model["model"])
            for model in candidates
        }
        passing = [
            model
            for model in result["models"]
            if (model.get("provider", "openai_compatible"), model["model"])
            in candidate_keys
            and not model_failed(model)
        ]
        if passing:
            print("Passing candidates:")
            for index, model in enumerate(passing, 1):
                print(f"  {index}. {model['provider']}:{model['model']}")
            answer = input("Approve a candidate number, or Enter to skip: ").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(passing):
                model = passing[int(answer) - 1]
                answer = (
                    input(f"Approve {model['provider']}:{model['model']}? [y/N]: ")
                    .strip()
                    .casefold()
                )
                if answer in {"y", "yes"}:
                    _approve_model_main(
                        [
                            f"{model['provider']}:{model['model']}",
                            "--from",
                            str(path),
                            "--approved",
                            str(args.against),
                        ]
                    )
    if result_failed(result):
        raise SystemExit(1)


def _approve_model_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Approve a tested model locally")
    parser.add_argument("model", help="provider:model")
    parser.add_argument("--from", dest="result_path", required=True, type=Path)
    parser.add_argument(
        "--approved", type=Path, default=Path("benchmark.approved.json")
    )
    parser.add_argument("--note")
    args = parser.parse_args(argv)
    provider, separator, model_id = args.model.partition(":")
    if not separator or not provider or not model_id:
        parser.error("model must use provider:model")
    result = load_json(args.result_path)
    match = next(
        (
            item
            for item in result.get("models", [])
            if item.get("provider") == provider and item.get("model") == model_id
        ),
        None,
    )
    if match is None:
        parser.error("source result does not contain the requested model")
    if model_failed(match):
        parser.error("refusing to approve a model with API or validation failures")
    approved = load_json(args.approved) if args.approved.exists() else {"models": []}
    models = approved.setdefault("models", [])
    if any(
        item.get("provider") == provider and item.get("model") == model_id
        for item in models
    ):
        parser.error("model is already approved")
    model_keys = (
        "provider",
        "model",
        "name",
        "base_url",
        "api_key_env",
        "api_version",
        "max_tokens_parameter",
        "capabilities",
        "supports_temperature",
        "input_cost_per_million",
        "output_cost_per_million",
    )
    models.append({key: match[key] for key in model_keys if key in match})
    approvals = approved.setdefault("approvals", [])
    approvals.append(
        {
            "provider": provider,
            "model": model_id,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "source_result": str(args.result_path),
            **({"note": args.note} if args.note else {}),
        }
    )
    args.approved.parent.mkdir(parents=True, exist_ok=True)
    args.approved.write_text(json.dumps(approved, indent=2) + "\n", encoding="utf-8")
    print(f"Approved {provider}:{model_id} in {args.approved}")


def _catalog_main(argv: list[str]) -> None:
    if not argv or argv[0] in {"-h", "--help"}:
        print(
            f"usage: {_display_command()} catalog {{init,refresh,prepare,probe,test}} ...\n\n"
            "init [DIRECTORY] [--providers PROVIDERS] [--replace]\n"
            "refresh WATCH_CONFIG [watch options]\n"
            "prepare WATCH_CONFIG --against APPROVED --output CONFIG [--replace]\n"
            "probe WATCH_CONFIG [--models LIST] [--ledger PATH]\n"
            "test WATCH_CONFIG --approved APPROVED --output CONFIG [--replace]"
        )
        return
    action, *rest = argv
    if action == "init":
        parser = argparse.ArgumentParser(prog=f"{_display_command()} catalog init")
        parser.add_argument(
            "directory", nargs="?", type=Path, default=Path("benchmarks")
        )
        parser.add_argument(
            "--providers",
            help="comma-separated providers to watch; skips the setup question",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="rewrite watch settings but keep approved models, keys, and results",
        )
        args = parser.parse_args(rest)
        sources = {
            "openai": {"provider": "openai", "include": "^gpt-", "limit": 50},
            "anthropic": {"provider": "anthropic", "limit": 30},
            "gemini": {"provider": "gemini", "include": "^gemini-", "limit": 30},
            "xai": {"provider": "xai", "include": "^grok-", "limit": 30},
            "openrouter": {
                "provider": "openrouter",
                "output_modalities": "text",
                "limit": 50,
            },
        }
        watch_path = args.directory / "watch.json"
        approved_path = args.directory / "approved.json"
        existing_workspace = watch_path.exists() or approved_path.exists()
        if existing_workspace and not args.replace:
            if args.providers is not None:
                raise ValueError(
                    f"{args.directory} already contains a catalog workspace; "
                    "use --replace to rewrite its watch settings"
                )
            answer = (
                input(
                    "A catalog workspace already exists. Rewrite watch settings and keep "
                    "approved models, keys, and results? [y/N]: "
                )
                .strip()
                .casefold()
            )
            if answer not in {"y", "yes"}:
                print(f"Kept existing catalog workspace in {args.directory}")
                return

        provider_answer = args.providers
        if provider_answer is None:
            print("=== Catalog setup ===")
            print("Choose provider catalogues now; this does not make paid requests.")
            provider_answer = input(
                "Providers to include (openai, anthropic, gemini, xai, openrouter, or all) [all]: "
            ).strip()
        if not provider_answer or provider_answer.casefold() == "all":
            names = list(sources)
        else:
            names = [
                name.strip() for name in provider_answer.split(",") if name.strip()
            ]
        unknown = sorted(set(names) - set(sources))
        if unknown:
            parser.error(f"unknown providers: {', '.join(unknown)}")
        args.directory.mkdir(parents=True, exist_ok=True)
        watch = {
            "name": "model-catalog-watch",
            "prompt": "Reply with ok.",
            "repetitions": 1,
            "warmups": 0,
            "concurrency": 1,
            "max_requests": 100,
            "request": {"temperature": 0, "max_output_tokens": 128},
            "discovery": [sources[name] for name in names],
        }
        watch_path.write_text(json.dumps(watch, indent=2) + "\n", encoding="utf-8")
        if not approved_path.exists():
            approved_path.write_text(
                '{"models": [], "approvals": []}\n', encoding="utf-8"
            )
        env_path = args.directory / ".env.production"
        if not env_path.exists():
            _create_catalog_env_file(env_path)
        (args.directory / "results").mkdir(exist_ok=True)
        print(
            f"{'Updated' if existing_workspace else 'Created'} catalog workspace in {args.directory}"
        )
        print(f"Next: {_display_command()} catalog refresh {watch_path}")
        return
    if action == "refresh":
        _watch_new_main(rest)
        return
    if action == "probe":
        parser = argparse.ArgumentParser(prog=f"{_display_command()} catalog probe")
        parser.add_argument("config", type=Path)
        parser.add_argument("--models", help="numbers, provider/family, or all")
        parser.add_argument("--ledger", type=Path)
        args = parser.parse_args(rest)
        load_env_file(args.config.resolve().parent / ".env.production")
        config = apply_provider_presets(apply_model_aliases(load_config(args.config)))
        models = apply_probe_evidence(
            resolve_models(config),
            load_ledger(args.config.parent / ".llm-preflight" / "capabilities.json"),
        )
        ready = [model for model in models if model.get("catalog_type") == "text-ready"]
        candidates = [
            model for model in models if model.get("catalog_type") == "text-candidate"
        ]
        other = len(models) - len(ready) - len(candidates)
        print("=== Catalogue capability review ===")
        print(f"Ready to benchmark: {len(ready)}")
        print(f"Needs one probe: {len(candidates)}")
        print(f"Not a generic text benchmark: {other}")
        if not candidates:
            return
        for index, model in enumerate(candidates, 1):
            print(f"  {index}. {model['provider']}:{model['model']}")
        answer = args.models
        if answer is None:
            answer = input(
                "Select text models to probe (numbers/provider/family/all), or Enter to cancel: "
            )
        selected = _select_numbered_models(candidates, answer, default_all=False)
        if not selected:
            print("Cancelled.")
            return
        print(f"This makes {len(selected)} minimal paid compatibility request(s).")
        if input("Run probes? [y/N]: ").strip().casefold() not in {"y", "yes"}:
            print("Cancelled.")
            return
        ledger = (
            args.ledger or args.config.parent / ".llm-preflight" / "capabilities.json"
        )
        with benchmark_run_lock(ledger.parent):
            for model in selected:
                probe = probe_model(model, ledger)
                print(f"{model['provider']}:{model['model']} → {probe['outcome']}")
        return
    if action == "test":
        parser = argparse.ArgumentParser(prog=f"{_display_command()} catalog test")
        parser.add_argument("config", type=Path)
        parser.add_argument("--approved", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument("--replace", action="store_true")
        args = parser.parse_args(rest)
        if args.output.exists() and not args.replace:
            raise ValueError(
                f"{args.output} already exists; use --replace to update it"
            )
        watch_config = apply_provider_presets(
            apply_model_aliases(load_config(args.config))
        )
        approved_config = load_json(args.approved)
        if not approved_config.get("models"):
            raise ValueError(f"{args.approved} has no approved models yet")
        config = build_candidate_config(watch_config, approved_config, [])
        args.output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote approved-model benchmark config to {args.output}")
        return
    parser = argparse.ArgumentParser(prog=f"{_display_command()} catalog {action}")
    parser.add_argument("config", type=Path)
    parser.add_argument("--against", type=Path, required=True)
    if action == "prepare":
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument(
            "--replace", action="store_true", help="replace an existing candidate plan"
        )
        args = parser.parse_args(rest)
        forwarded = [
            str(args.config),
            "--against",
            str(args.against),
            "--write-config",
            str(args.output),
            "--all-unapproved",
            "--select-candidates",
        ]
        if args.replace:
            forwarded.append("--replace")
        _watch_new_main(forwarded)
        return
    parser.error("choose refresh or prepare")


def _models_main(argv: list[str]) -> None:
    if argv and argv[0] == "approve":
        _approve_model_main(argv[1:])
        return
    if argv and argv[0] == "remove":
        _remove_model_main(argv[1:])
        return
    raise ValueError(
        "models command requires: approve PROVIDER:MODEL --from RESULT, or remove PROVIDER:MODEL"
    )


def _remove_model_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Remove a permanent approved model")
    parser.add_argument("model", help="provider:model")
    parser.add_argument(
        "--approved", type=Path, default=Path("benchmark.approved.json")
    )
    parser.add_argument("--note")
    args = parser.parse_args(argv)
    provider, separator, model_id = args.model.partition(":")
    if not separator or not provider or not model_id:
        parser.error("model must use provider:model")
    approved = load_json(args.approved)
    models = approved.get("models", [])
    matching = [
        model
        for model in models
        if model.get("provider") == provider and model.get("model") == model_id
    ]
    if not matching:
        parser.error("model is not approved")
    answer = (
        input(f"Remove {provider}:{model_id} from {args.approved}? [y/N]: ")
        .strip()
        .casefold()
    )
    if answer not in {"y", "yes"}:
        print("Cancelled.")
        return
    approved["models"] = [model for model in models if model not in matching]
    approved.setdefault("removals", []).append(
        {
            "provider": provider,
            "model": model_id,
            "removed_at": datetime.now(timezone.utc).isoformat(),
            **({"note": args.note} if args.note else {}),
        }
    )
    args.approved.write_text(json.dumps(approved, indent=2) + "\n", encoding="utf-8")
    print(f"Removed {provider}:{model_id} from {args.approved}")


def interactive_promote_models(
    result: dict[str, Any],
    result_path: Path,
    approved_path: Path,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    approved = load_json(approved_path) if approved_path.exists() else {"models": []}
    approved_keys = {
        (item.get("provider", "openai_compatible"), item["model"])
        for item in approved.get("models", [])
    }
    passing = [
        model
        for model in result.get("models", [])
        if not model_failed(model)
        and (model.get("provider", "openai_compatible"), model["model"])
        not in approved_keys
    ]
    if not passing:
        return
    output_fn("Passing models available to add to permanent approved tests:")
    for index, model in enumerate(passing, 1):
        output_fn(f"  {index}. {model['provider']}:{model['model']}")
    selected = _select_numbered_models(
        passing,
        input_fn("Add model numbers, or Enter to skip: "),
        default_all=False,
    )
    if not selected:
        return
    names = ", ".join(f"{model['provider']}:{model['model']}" for model in selected)
    if input_fn(f"Add {names} to {approved_path}? [y/N]: ").strip().casefold() not in {
        "y",
        "yes",
    }:
        output_fn("Cancelled.")
        return
    for model in selected:
        _approve_model_main(
            [
                f"{model['provider']}:{model['model']}",
                "--from",
                str(result_path),
                "--approved",
                str(approved_path),
            ]
        )


def _style(text: str, code: str, color: bool) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if color else text


def _stage_heading(title: str, color_code: str, color: bool) -> str:
    return _style(f"=== {title} ===", f"1;{color_code}", color)


def _clear_screen() -> None:
    print("\x1b[2J\x1b[H", end="")


def interactive_selection(
    config: dict[str, Any],
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    color: bool = False,
    clear_fn: Callable[[], None] | None = None,
    selected_models: list[dict[str, Any]] | None = None,
    selected_profile_selector: str | None = None,
) -> tuple[dict[str, Any], str | None] | None:
    if clear_fn:
        clear_fn()
    if selected_models is None:
        models = resolve_models(config)
        if not models:
            raise ValueError("model discovery returned no models")

        output_fn(_stage_heading("Models", "36", color))
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
            selectors = [
                item.strip() for item in model_answer.split(",") if item.strip()
            ]
            for selector in selectors:
                if selector.isdigit():
                    selected_indexes.update(_selected_numbers(selector, len(models)))
                    continue
                provider, separator, family = selector.casefold().partition("/")
                provider_matches = {
                    index
                    for index, model in enumerate(models)
                    if model.get("provider", "openai_compatible").casefold() == provider
                }
                exact_matches = {
                    index
                    for index in provider_matches
                    if separator and models[index]["model"].casefold() == family
                }
                matches = exact_matches or {
                    index
                    for index in provider_matches
                    if not separator
                    or models[index]["model"].casefold().startswith(family + "/")
                    or models[index]["model"].casefold().startswith(family + "-")
                }
                if not matches:
                    raise ValueError(f"model selector {selector!r} matched no models")
                selected_indexes.update(matches)
            selected_models = [
                model for index, model in enumerate(models) if index in selected_indexes
            ]

    if selected_profile_selector is None:
        output_fn("")
        output_fn(_stage_heading("Tests", "35", color))
        tests: list[tuple[str, str, str]] = []
        for profile in BUILTIN_PROFILES:
            tests.append(("profile", profile["name"], profile["description"]))
        custom_prompts = config.get("prompts", [])
        for prompt in custom_prompts:
            tests.append(
                (
                    "custom",
                    prompt["name"],
                    prompt.get("description", "Custom prompt test."),
                )
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
            profile_selector = ",".join(
                profile["name"]
                for profile in BUILTIN_PROFILES
                if "concurrency_levels" not in profile
            )
            profile_selector = ",".join(
                [profile_selector] + [prompt["name"] for prompt in custom_prompts]
            )
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
    else:
        profile_selector = selected_profile_selector

    default_repetitions = int(
        config.get(
            "suite_repetitions" if profile_selector else "repetitions",
            1 if profile_selector else 5,
        )
    )
    output_fn("")
    output_fn(_stage_heading("Repetitions", "34", color))
    repetitions_answer = input_fn(
        f"Repetitions per test [{default_repetitions}]: "
    ).strip()
    repetitions = int(repetitions_answer) if repetitions_answer else default_repetitions
    if repetitions < 1:
        raise ValueError("repetitions must be positive")

    output_fn("")
    output_fn(_stage_heading("Stop Mode", "31", color))
    output_fn("  1. any-fail — stop after API error or TEST FAIL")
    output_fn("  2. api-error — stop only if the provider/request breaks")
    output_fn("  3. test-fail — stop on model output failure, ignore API-ok passes")
    output_fn("  4. never — run every selected model")
    stop_answer = input_fn("Stop on [1]: ").strip().casefold()
    if stop_answer in {"", "1", "any-fail"}:
        stop_on = "any-fail"
    elif stop_answer in {"2", "api-error"}:
        stop_on = "api-error"
    elif stop_answer in {"3", "test-fail"}:
        stop_on = "test-fail"
    elif stop_answer in {"4", "never", "none"}:
        stop_on = None
    elif stop_answer in {"y", "yes", "n", "no"}:
        # A familiar confirmation answer here must never authorize spending.
        # Keep the safe default and still ask the separate paid-run question.
        stop_on = "any-fail"
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
    output_fn(_stage_heading("Run Plan", "36", color))
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
        output_fn("")
        output_fn("Request breakdown per model:")
        for breakdown_item in _test_breakdown(selected_config, profile_selector):
            output_fn(
                f"  {breakdown_item['name']}: "
                f"{breakdown_item['requests_per_model']} ({breakdown_item['details']})"
            )
        output_fn("")
    if not confirm_interactive_budget(
        selected_config,
        profile_selector,
        input_fn=input_fn,
        output_fn=output_fn,
        color=color,
    ):
        return None
    confirmation_answer = input_fn("Run paid benchmark? [y/N]: ").strip().casefold()
    if confirmation_answer not in {"y", "yes"}:
        output_fn(_style("Cancelled.", "1;33", color))
        return None
    return selected_config, profile_selector


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "catalog":
        try:
            _catalog_main(sys.argv[2:])
        except (KeyboardInterrupt, EOFError):
            print("Benchmark cancelled; no artifacts saved.", file=sys.stderr)
            raise SystemExit(130) from None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(
                f"{_display_command()}: error: {redact_secrets(str(exc))}",
                file=sys.stderr,
            )
            raise SystemExit(2) from None
        return
    if len(sys.argv) > 1 and sys.argv[1] == "models":
        try:
            _models_main(sys.argv[2:])
        except (KeyboardInterrupt, EOFError):
            print("Benchmark cancelled; no artifacts saved.", file=sys.stderr)
            raise SystemExit(130) from None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(
                f"{_display_command()}: error: {redact_secrets(str(exc))}",
                file=sys.stderr,
            )
            raise SystemExit(2) from None
        return
    if len(sys.argv) > 1 and sys.argv[1] == "watch-new":
        try:
            _watch_new_main(sys.argv[2:])
        except (KeyboardInterrupt, EOFError):
            print("Benchmark cancelled; no artifacts saved.", file=sys.stderr)
            raise SystemExit(130) from None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(
                f"{_display_command()}: error: {redact_secrets(str(exc))}",
                file=sys.stderr,
            )
            raise SystemExit(2) from None
        return
    if len(sys.argv) > 1 and sys.argv[1] == "approve-model":
        try:
            _approve_model_main(sys.argv[2:])
        except (KeyboardInterrupt, EOFError):
            print("Benchmark cancelled; no artifacts saved.", file=sys.stderr)
            raise SystemExit(130) from None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(
                f"{_display_command()}: error: {redact_secrets(str(exc))}",
                file=sys.stderr,
            )
            raise SystemExit(2) from None
        return
    parser = argparse.ArgumentParser(description="Benchmark configurable LLM providers")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
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
        "--smoke",
        action="store_true",
        help="run a reduced live benchmark: one repetition, no warmups",
    )
    parser.add_argument(
        "--migration-check",
        action="store_true",
        help="run the fast three-case response-contract preflight",
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
        "--init",
        nargs="?",
        const=Path("benchmark.json"),
        type=Path,
        help="create a no-key mock benchmark configuration",
    )
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
    parser.add_argument(
        "--approve-to",
        type=Path,
        help="after an interactive saved run, offer passing models for approval here",
    )
    args = parser.parse_args()
    path: Path | None = None
    try:
        if args.init is not None:
            if args.config is not None:
                parser.error("--init cannot be combined with a benchmark configuration")
            _write_starter_config(args.init)
            command = _display_command()
            print(f"Created {args.init}")
            print(f"Run the no-key demo: {command} {args.init} --no-save")
            print(f"Explore interactively: {command} {args.init} --interactive")
            return
        if args.no_env_file and args.env_file:
            parser.error("--no-env-file cannot be combined with --env-file")
        if args.diff:
            diff = compare_results(load_json(args.diff[0]), load_json(args.diff[1]))
            print(json.dumps(diff, indent=2) if args.json else _format_diff(diff))
            if args.ci and not diff["ok"]:
                raise SystemExit(1)
            return
        if args.migration_check and args.quick:
            parser.error("--migration-check requires a benchmark configuration")
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
        validate_config_validations(config)
        if args.profiles and args.tests:
            parser.error("--profiles cannot be combined with --tests")
        if args.migration_check and (args.profiles or args.tests or args.prompt_name):
            parser.error(
                "--migration-check cannot be combined with --profiles, --tests, or --prompt"
            )
        if args.profiles and args.prompt_name:
            parser.error("--profiles cannot be combined with --prompt")
        if args.tests and args.prompt_name:
            parser.error("--tests cannot be combined with --prompt")
        if args.interactive and (
            args.catalog
            or args.profiles
            or args.tests
            or args.prompt_name
            or args.migration_check
        ):
            parser.error(
                "--interactive cannot be combined with --catalog, --profiles, "
                "--tests, --prompt, or --migration-check"
            )
        if args.approve_to and not args.interactive:
            parser.error("--approve-to requires --interactive")
        if args.approve_to and args.no_save:
            parser.error("--approve-to cannot be combined with --no-save")
        if args.smoke:
            config = apply_smoke_mode(config)
            config.setdefault("save_responses", "failures")
        if args.migration_check:
            config = apply_migration_check(config)
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
        profile_selector = (
            "quick-migration-check"
            if args.migration_check
            else args.tests or args.profiles
        )
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
            plan = _dry_run_plan(config, profile_selector)
            print(
                json.dumps(plan, indent=2) if args.json else _format_dry_run_plan(plan),
                end="" if not args.json else "\n",
            )
            return
        check_budget(_budget_config(config, profile_selector))
        use_color = sys.stdout.isatty()
        with benchmark_run_lock(args.output_dir):
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
    except (KeyboardInterrupt, EOFError):
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
    if args.approve_to and path is not None:
        interactive_promote_models(result, path, args.approve_to)
    if result_failed(result):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
