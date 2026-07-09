from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .catalog import resolve_models
from .env import load_env_file
from .profiles import BUILTIN_PROFILES
from .runner import (
    console_report,
    load_config,
    run_benchmark,
    save_result,
    select_custom_prompt,
)


def catalog_output(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return catalog entries safe for terminal/CI output."""
    return [
        {key: value for key, value in model.items() if key != "headers"}
        for model in models
    ]


def format_progress_event(event: dict[str, Any]) -> str:
    if event["type"] == "model_start":
        return (
            f"Model {event['model_index']}/{event['model_total']}: "
            f"{event['provider']} — {event['model']} "
            f"({event['request_total']} requests)"
        )
    if event["type"] == "request_complete":
        status = event["status"].upper()
        if event.get("error"):
            status += f" ({event['error']})"
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
        )
    if event["type"] == "model_complete":
        cost = event.get("estimated_cost_usd")
        return (
            f"  Done: {event['successful']}/{event['requests']} successful, "
            f"{event['failed']} failed | tokens in/out "
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


def interactive_selection(
    config: dict[str, Any],
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> tuple[dict[str, Any], str | None] | None:
    models = resolve_models(config)
    if not models:
        raise ValueError("model discovery returned no models")

    output_fn("Models:")
    for index, model in enumerate(models, 1):
        provider = model.get("provider", "openai_compatible")
        output_fn(f"  {index}. {provider}: {model['model']}")
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

    output_fn("Profiles:")
    for index, profile in enumerate(BUILTIN_PROFILES, 1):
        output_fn(f"  {index}. {profile['name']} — {profile['description']}")
    custom_prompts = config.get("prompts", [])
    if custom_prompts:
        output_fn("Custom prompts:")
        for prompt in custom_prompts:
            output_fn(f"  {prompt['name']}")
    profile_answer = input_fn(
        "Select profiles (numbers/all), a custom prompt name, "
        "or Enter for the config prompt: "
    ).strip()
    if profile_answer.casefold() == "all":
        profile_selector = "all"
    elif profile_answer and all(
        item.strip().isdigit() for item in profile_answer.split(",")
    ):
        profile_selector = ",".join(
            BUILTIN_PROFILES[index]["name"]
            for index in _selected_numbers(profile_answer, len(BUILTIN_PROFILES))
        )
    elif profile_answer:
        config = select_custom_prompt(config, profile_answer)
        profile_selector = None
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
    repetitions_answer = input_fn(
        f"Repetitions per test [{default_repetitions}]: "
    ).strip()
    repetitions = int(repetitions_answer) if repetitions_answer else default_repetitions
    if repetitions < 1:
        raise ValueError("repetitions must be positive")

    selected_config = dict(config)
    selected_config["models"] = selected_models
    selected_config["discovery"] = []
    selected_config["repetitions"] = repetitions
    selected_config["suite_repetitions"] = repetitions
    profile_label = profile_selector or selected_config.get(
        "prompt_name", "config prompt"
    )
    output_fn(
        f"Ready: {len(selected_models)} models, {profile_label}, "
        f"{repetitions} repetitions."
    )
    if input_fn("Run paid benchmark? [y/N]: ").strip().casefold() not in {"y", "yes"}:
        output_fn("Cancelled.")
        return None
    return selected_config, profile_selector


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark configurable LLM providers")
    parser.add_argument("config", type=Path, help="benchmark JSON configuration")
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--json", action="store_true", help="print full JSON result")
    parser.add_argument(
        "--catalog",
        action="store_true",
        help="discover and print selected models without benchmarking them",
    )
    parser.add_argument(
        "--profiles",
        help="comma-separated built-in profiles, or 'all' for the mixed suite",
    )
    parser.add_argument(
        "--prompt",
        dest="prompt_name",
        help="run one named custom prompt from the config",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="interactively select models, profiles, and repetitions",
    )
    args = parser.parse_args()
    try:
        load_env_file(args.config.resolve().parent / ".env.production")
        config = load_config(args.config)
        if args.profiles and args.prompt_name:
            parser.error("--profiles cannot be combined with --prompt")
        if args.interactive and (args.catalog or args.profiles or args.prompt_name):
            parser.error(
                "--interactive cannot be combined with --catalog, --profiles, "
                "or --prompt"
            )
        if args.catalog:
            print(json.dumps(catalog_output(resolve_models(config)), indent=2))
            return
        profile_selector = args.profiles
        if args.prompt_name:
            config = select_custom_prompt(config, args.prompt_name)
        if args.interactive:
            selection = interactive_selection(config)
            if selection is None:
                return
            config, profile_selector = selection
        result = run_benchmark(
            config,
            profile_selector=profile_selector,
            progress=(
                (lambda event: print(format_progress_event(event), flush=True))
                if args.interactive
                else None
            ),
        )
        path = save_result(result, args.output_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(result, indent=2)
        if args.json
        else console_report(result, color=sys.stdout.isatty())
    )
    print(f"Saved raw result to {path}", file=sys.stderr)
    if any(model["summary"]["failed"] for model in result["models"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
