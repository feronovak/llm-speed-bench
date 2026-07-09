from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import resolve_models
from .client import create_client
from .metrics import summarize
from .profiles import evaluate_response, select_profiles


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text())
    prompts = config.get("prompts", [])
    if "prompt" not in config and not prompts:
        raise ValueError("config requires 'prompt' or 'prompts'")
    if not isinstance(prompts, list):
        raise ValueError("'prompts' must be a list")
    prompt_names = []
    for index, prompt in enumerate(prompts):
        if not isinstance(prompt, dict):
            raise ValueError(f"prompts[{index}] must be an object")
        if not isinstance(prompt.get("name"), str) or not prompt["name"]:
            raise ValueError(f"prompts[{index}] requires 'name'")
        if not isinstance(prompt.get("prompt"), str) or not prompt["prompt"]:
            raise ValueError(f"prompts[{index}] requires a non-empty 'prompt'")
        prompt_names.append(prompt["name"])
    if len(prompt_names) != len(set(prompt_names)):
        raise ValueError("custom prompt names must be unique")
    if not config.get("models") and not config.get("discovery"):
        raise ValueError("config requires 'models' or 'discovery'")
    for index, model in enumerate(config.get("models", [])):
        if "model" not in model:
            raise ValueError(f"models[{index}] requires 'model'")
    return config


def select_custom_prompt(config: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [prompt for prompt in config.get("prompts", []) if prompt["name"] == name]
    if not matches:
        available = ", ".join(prompt["name"] for prompt in config.get("prompts", []))
        suffix = f"; choose {available}" if available else ""
        raise ValueError(f"unknown custom prompt {name!r}{suffix}")
    prompt = matches[0]
    selected = dict(config)
    selected["prompt_name"] = name
    selected["prompt"] = prompt["prompt"]
    if "request" in prompt:
        selected["request"] = dict(prompt["request"])
    if prompt.get("system_prompt"):
        selected.setdefault("request", {})
        selected["request"] = dict(selected["request"])
        selected["request"]["system_prompt"] = prompt["system_prompt"]
    if "validation" in prompt:
        selected["validation"] = dict(prompt["validation"])
    return selected


def _validate(sample: dict[str, Any], validation: dict[str, Any]) -> None:
    if not sample["ok"]:
        return
    response = sample["response"]
    if "contains" in validation and validation["contains"] not in response:
        sample["ok"] = False
        sample["error"] = f"response did not contain {validation['contains']!r}"
    if "regex" in validation and not re.search(validation["regex"], response):
        sample["ok"] = False
        sample["error"] = f"response did not match regex {validation['regex']!r}"


def _execute(
    client: Any,
    jobs: list[tuple[dict[str, Any], dict[str, Any], int | None]],
    concurrency: int,
    save_responses: bool,
    on_complete: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(client.run, case["prompt"], options): (case, level)
            for case, options, level in jobs
        }
        for future in as_completed(futures):
            case, level = futures[future]
            sample = future.result()
            response = sample.get("response", "")
            evaluation = (
                evaluate_response(response, case["evaluator"])
                if sample["ok"]
                else {"score": 0.0, "valid": False, "error": "request failed"}
            )
            sample.update(
                {
                    "case_id": case["id"],
                    "quality_score": evaluation["score"],
                    "valid_output": evaluation["valid"],
                    "evaluation_error": evaluation["error"],
                }
            )
            if level is not None:
                sample["concurrency"] = level
            if not save_responses:
                sample.pop("response", None)
            samples.append(sample)
            if on_complete:
                on_complete(sample, case)
    return samples


def _profile_summary(
    samples: list[dict[str, Any]], model: dict[str, Any]
) -> dict[str, Any]:
    summary = summarize(samples, model)
    summary["quality_score"] = (
        sum(sample["quality_score"] for sample in samples) / len(samples)
        if samples
        else 0
    )
    summary["valid_output_rate"] = (
        sum(bool(sample["valid_output"]) for sample in samples) / len(samples)
        if samples
        else 0
    )
    return summary


def _profile_progress_callback(
    callback: Callable[[dict[str, Any], str], None] | None,
    profile_name: str,
) -> Callable[[dict[str, Any], dict[str, Any]], None] | None:
    if callback is None:
        return None

    def report_case(sample: dict[str, Any], case: dict[str, Any]) -> None:
        callback(sample, f"{profile_name}/{case['id']}")

    return report_case


def _run_profiles(
    client: Any,
    model: dict[str, Any],
    profiles: list[dict[str, Any]],
    config: dict[str, Any],
    request_options: dict[str, Any],
    warmups: int,
    warmup_samples: list[dict[str, Any]],
    on_complete: Callable[[dict[str, Any], str], None] | None = None,
) -> list[dict[str, Any]]:
    repetitions = int(config.get("suite_repetitions", 1))
    save_responses = bool(config.get("save_responses", False))
    results = []
    for profile in profiles:
        progress_callback = _profile_progress_callback(on_complete, profile["name"])
        options = dict(request_options)
        options.update(profile.get("request", {}))
        if profile.get("system_prompt"):
            options["system_prompt"] = profile["system_prompt"]
        for _ in range(warmups):
            warmup_samples.append(client.run(profile["cases"][0]["prompt"], options))

        if profile["name"] == "load":
            samples = []
            by_concurrency = []
            for level in profile["concurrency_levels"]:
                request_count = max(repetitions, level)
                jobs = [
                    (profile["cases"][index % len(profile["cases"])], options, level)
                    for index in range(request_count)
                ]
                level_samples = _execute(
                    client,
                    jobs,
                    level,
                    save_responses=save_responses,
                    on_complete=progress_callback,
                )
                samples.extend(level_samples)
                by_concurrency.append(
                    {
                        "concurrency": level,
                        "summary": _profile_summary(level_samples, model),
                    }
                )
        else:
            jobs = [
                (case, options, None)
                for case in profile["cases"]
                for _ in range(repetitions)
            ]
            samples = _execute(
                client,
                jobs,
                1,
                save_responses=save_responses,
                on_complete=progress_callback,
            )
            by_concurrency = None

        result = {
            "name": profile["name"],
            "description": profile["description"],
            "dataset_version": 1,
            "samples": samples,
            "summary": _profile_summary(samples, model),
        }
        if by_concurrency is not None:
            result["by_concurrency"] = by_concurrency
        results.append(result)
    return results


def _sample_cost(sample: dict[str, Any], model: dict[str, Any]) -> float | None:
    input_price = model.get("input_cost_per_million")
    output_price = model.get("output_cost_per_million")
    if input_price is None or output_price is None:
        return None
    return (
        float(sample.get("input_tokens") or 0) * float(input_price)
        + float(sample.get("output_tokens") or 0) * float(output_price)
    ) / 1_000_000


def _profile_request_count(profiles: list[dict[str, Any]], repetitions: int) -> int:
    return sum(
        sum(max(repetitions, level) for level in profile["concurrency_levels"])
        if profile["name"] == "load"
        else len(profile["cases"]) * repetitions
        for profile in profiles
    )


def run_benchmark(
    config: dict[str, Any],
    profile_selector: str | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if "prompt" not in config:
        raise ValueError("select a custom prompt before running the benchmark")
    prompt = config["prompt"]
    repetitions = int(config.get("repetitions", 5))
    warmups = int(config.get("warmups", 1))
    concurrency = int(config.get("concurrency", 1))
    timeout = float(config.get("timeout_seconds", 120))
    request_options = config.get(
        "request", {"temperature": 0, "max_output_tokens": 256}
    )
    validation = config.get("validation", {})
    configured_profiles = config.get("profiles")
    if profile_selector is None and configured_profiles:
        profile_selector = (
            ",".join(configured_profiles)
            if isinstance(configured_profiles, list)
            else str(configured_profiles)
        )
    profiles = select_profiles(profile_selector) if profile_selector else []
    models_result = []
    models = resolve_models(config)
    if not models:
        raise ValueError("model discovery returned no models")

    for model_index, model in enumerate(models, 1):
        request_total = (
            _profile_request_count(profiles, int(config.get("suite_repetitions", 1)))
            if profiles
            else repetitions
        )
        if progress:
            progress(
                {
                    "type": "model_start",
                    "model_index": model_index,
                    "model_total": len(models),
                    "provider": model.get("provider", "openai_compatible"),
                    "model": model["model"],
                    "request_total": request_total,
                }
            )
        client = create_client(model, timeout)
        warmup_samples: list[dict[str, Any]] = []
        completed_requests = 0

        def report_sample(sample: dict[str, Any], phase: str) -> None:
            nonlocal completed_requests
            completed_requests += 1
            if progress:
                progress(
                    {
                        "type": "request_complete",
                        "request_index": completed_requests,
                        "request_total": request_total,
                        "phase": phase,
                        "status": "ok" if sample["ok"] else "error",
                        "input_tokens": sample.get("input_tokens"),
                        "output_tokens": sample.get("output_tokens"),
                        "estimated_cost_usd": _sample_cost(sample, model),
                        "error": sample.get("error"),
                    }
                )

        profile_results = (
            _run_profiles(
                client,
                model,
                profiles,
                config,
                request_options,
                warmups,
                warmup_samples,
                on_complete=report_sample,
            )
            if profiles
            else []
        )
        if profiles:
            samples = [
                sample
                for profile_result in profile_results
                for sample in profile_result["samples"]
            ]
        else:
            for _ in range(warmups):
                warmup_samples.append(client.run(prompt, request_options))
            samples = []
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(client.run, prompt, request_options)
                    for _ in range(repetitions)
                ]
                for future in as_completed(futures):
                    sample = future.result()
                    _validate(sample, validation)
                    report_sample(sample, config.get("prompt_name", "config-prompt"))
                    if not config.get("save_responses", False):
                        sample.pop("response", None)
                    samples.append(sample)
        model_result = {
            "name": model.get("name", model["model"]),
            "model": model["model"],
            "provider": model.get("provider", "openai_compatible"),
            "base_url": client.model["base_url"],
            "capabilities": model.get("capabilities", {}),
            "context_length": model.get("context_length"),
            "max_output_tokens": model.get("max_output_tokens"),
            "catalog_metadata": model.get("catalog_metadata"),
            "samples": samples,
            "summary": summarize(samples, model),
            "warmup_summary": summarize(warmup_samples, model),
        }
        if profiles:
            model_result["profiles"] = profile_results
        models_result.append(model_result)
        if progress:
            progress(
                {
                    "type": "model_complete",
                    "model": model["model"],
                    **{
                        key: model_result["summary"][key]
                        for key in (
                            "requests",
                            "successful",
                            "failed",
                            "input_tokens",
                            "output_tokens",
                            "estimated_cost_usd",
                        )
                    },
                }
            )

    costs = [
        cost
        for model in models_result
        for cost in (
            model["summary"]["estimated_cost_usd"],
            model["warmup_summary"]["estimated_cost_usd"],
        )
    ]
    result = {
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmark": config.get("name", "llm-benchmark"),
        "prompt_name": config.get("prompt_name"),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "prompt_chars": len(prompt),
        "settings": {
            "repetitions": repetitions,
            "warmups": warmups,
            "concurrency": concurrency,
            "timeout_seconds": timeout,
            "request": request_options,
            "profiles": [profile["name"] for profile in profiles],
            "suite_repetitions": int(config.get("suite_repetitions", 1)),
        },
        "environment": {
            "hostname": platform.node(),
            "python": platform.python_version(),
        },
        "models": models_result,
        "total_input_tokens": sum(
            model[summary]["input_tokens"]
            for model in models_result
            for summary in ("summary", "warmup_summary")
        ),
        "total_output_tokens": sum(
            model[summary]["output_tokens"]
            for model in models_result
            for summary in ("summary", "warmup_summary")
        ),
        "total_estimated_cost_usd": (
            sum(costs) if all(cost is not None for cost in costs) else None
        ),
    }
    return result


def save_result(result: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    stamp = result["timestamp"].replace(":", "").replace("+00:00", "Z")
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", result["benchmark"]).strip("-")
    path = output_dir / f"{stamp}-{name}-{result['run_id'][:8]}.json"
    _write_private(path, json.dumps(result, indent=2) + "\n")
    _write_private(path.with_suffix(".md"), report(result))
    return path


def _write_private(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(content)


def format_seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}s"


def _ranking_metrics(model: dict[str, Any]) -> dict[str, Any]:
    profiles = model.get("profiles") or []
    if not profiles:
        summary = model["summary"]
        return {
            "name": model.get("name", model.get("model", "unknown")),
            "requests": summary.get("requests", 0),
            "reliability": summary.get("success_rate", 0),
            "latency": summary.get("latency_seconds", {}).get("mean"),
            "cost": summary.get("estimated_cost_usd"),
        }

    requests = sum(profile["summary"]["requests"] for profile in profiles)
    weighted_latency = sum(
        (profile["summary"]["latency_seconds"].get("mean") or 0)
        * profile["summary"]["requests"]
        for profile in profiles
    )
    valid = sum(
        profile["summary"].get("valid_output_rate", profile["summary"]["success_rate"])
        * profile["summary"]["requests"]
        for profile in profiles
    )
    costs = [profile["summary"].get("estimated_cost_usd") for profile in profiles]
    return {
        "name": model.get("name", model.get("model", "unknown")),
        "requests": requests,
        "reliability": valid / requests if requests else 0,
        "latency": weighted_latency / requests if requests else None,
        "cost": sum(costs) if all(cost is not None for cost in costs) else None,
    }


def _executive_summary(result: dict[str, Any]) -> list[str]:
    metrics = [_ranking_metrics(model) for model in result["models"]]
    timed = [
        item
        for item in metrics
        if item["latency"] is not None and item["requests"] and item["reliability"] > 0
    ]
    priced = [
        item
        for item in metrics
        if item["cost"] is not None and item["requests"] and item["reliability"] > 0
    ]
    lines = ["", "## Executive summary", ""]
    if timed:
        fastest = min(timed, key=lambda item: item["latency"])
        lines.append(
            f"- Fastest: **{fastest['name']}** — "
            f"{fastest['latency']:.3f}s mean latency."
        )
    else:
        lines.append("- Fastest: unavailable; no successful timed requests.")
    if priced:
        cheapest = min(priced, key=lambda item: item["cost"])
        lines.append(
            f"- Cheapest: **{cheapest['name']}** — ${cheapest['cost']:.6f} total."
        )
    else:
        lines.append("- Cheapest: unavailable; no pricing data.")
    value_candidates = [
        item
        for item in priced
        if item["latency"] is not None and item["reliability"] > 0
    ]
    if value_candidates:
        min_latency = min(item["latency"] for item in value_candidates)
        min_cost = min(item["cost"] for item in value_candidates)
        for item in value_candidates:
            speed = min_latency / item["latency"]
            cost = min_cost / item["cost"] if item["cost"] else 1.0
            item["value_score"] = (item["reliability"] + speed + cost) / 3
        best = max(value_candidates, key=lambda item: item["value_score"])
        lines.append(
            f"- Best value: **{best['name']}** — "
            f"{best['value_score']:.0%} composite score."
        )
    else:
        lines.append("- Best value: unavailable; latency and pricing are required.")
    total_cost = result.get("total_estimated_cost_usd")
    lines.append(
        "- Total spent: "
        + (
            f"**${total_cost:.6f}** including warmups."
            if total_cost is not None
            else "unavailable; one or more models lack pricing."
        )
    )
    lines.extend(
        [
            "",
            "Value equally weights valid-output reliability, relative speed, and relative cost.",
        ]
    )
    return lines


def report(result: dict[str, Any]) -> str:
    profile_mode = any("profiles" in model for model in result["models"])
    prompt_label = (
        f"**{result['prompt_name']}** (`{result['prompt_sha256'][:12]}`)"
        if result.get("prompt_name")
        else f"`{result['prompt_sha256'][:12]}`"
    )
    lines = [
        f"# {result['benchmark']}",
        "",
        f"Run: `{result['run_id']}`  ",
        f"Time: {result['timestamp']}  ",
        f"Prompt: {prompt_label}",
        "",
    ]
    if profile_mode:
        lines.extend(
            [
                "| Model | Profile | Quality | Success | Latency p95 | TTFT p50 | Tokens/s p50 | Cost |",
                "|---|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
    else:
        lines.extend(
            [
                "| Model | Success | Latency p50 | Latency p95 | TTFT p50 | Tokens/s p50 | Cost |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
    for model in result["models"]:
        if profile_mode:
            for profile in model["profiles"]:
                summary = profile["summary"]
                cost = summary["estimated_cost_usd"]
                token_rate = summary["output_tokens_per_second"]["p50"]
                lines.append(
                    f"| {model['name']} | {profile['name']} "
                    f"| {summary['quality_score']:.0%} "
                    f"| {summary['success_rate']:.0%} "
                    f"| {format_seconds(summary['latency_seconds']['p95'])} "
                    f"| {format_seconds(summary['ttft_seconds']['p50'])} "
                    f"| {'n/a' if token_rate is None else f'{token_rate:.1f}'} "
                    f"| {'n/a' if cost is None else f'${cost:.6f}'} |"
                )
            continue
        summary = model["summary"]
        cost = summary["estimated_cost_usd"]
        token_rate = summary["output_tokens_per_second"]["p50"]
        token_rate_text = "n/a" if token_rate is None else f"{token_rate:.1f}"
        cost_text = "n/a" if cost is None else f"${cost:.6f}"
        lines.append(
            f"| {model['name']} | {summary['success_rate']:.0%} "
            f"| {format_seconds(summary['latency_seconds']['p50'])} "
            f"| {format_seconds(summary['latency_seconds']['p95'])} "
            f"| {format_seconds(summary['ttft_seconds']['p50'])} "
            f"| {token_rate_text} | {cost_text} |"
        )
    lines.extend(_executive_summary(result))
    return "\n".join(lines) + "\n"


def _terminal_table(
    headers: list[str], rows: list[list[str]], row_colors: list[str] | None = None
) -> list[str]:
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        if rows
        else len(headers[index])
        for index in range(len(headers))
    ]

    def border(left: str, middle: str, right: str) -> str:
        return left + middle.join("─" * (width + 2) for width in widths) + right

    def row_line(row: list[str]) -> str:
        return (
            "│ "
            + " │ ".join(value.ljust(widths[index]) for index, value in enumerate(row))
            + " │"
        )

    lines = [
        border("┌", "┬", "┐"),
        row_line(headers),
        border("├", "┼", "┤"),
    ]
    for index, row in enumerate(rows):
        line = row_line(row)
        color = row_colors[index] if row_colors else ""
        lines.append(f"{color}{line}\x1b[0m" if color else line)
    lines.append(border("└", "┴", "┘"))
    return lines


def console_report(result: dict[str, Any], color: bool = False) -> str:
    profile_mode = any("profiles" in model for model in result["models"])
    rows: list[list[str]] = []
    statuses: list[float] = []
    if profile_mode:
        headers = [
            "Model",
            "Profile",
            "Quality",
            "Reliable",
            "Latency p95",
            "TTFT p50",
            "Tok/s",
            "Cost",
        ]
        for model in result["models"]:
            for profile in model["profiles"]:
                summary = profile["summary"]
                cost = summary["estimated_cost_usd"]
                rate = summary["output_tokens_per_second"]["p50"]
                reliability = summary.get("valid_output_rate", summary["success_rate"])
                rows.append(
                    [
                        model["name"],
                        profile["name"],
                        f"{summary.get('quality_score', 0):.0%}",
                        f"{reliability:.0%}",
                        format_seconds(summary["latency_seconds"]["p95"]),
                        format_seconds(summary["ttft_seconds"]["p50"]),
                        "n/a" if rate is None else f"{rate:.1f}",
                        "n/a" if cost is None else f"${cost:.6f}",
                    ]
                )
                statuses.append(reliability)
    else:
        headers = [
            "Model",
            "Success",
            "Latency p50",
            "Latency p95",
            "TTFT p50",
            "Tok/s",
            "Cost",
        ]
        for model in result["models"]:
            summary = model["summary"]
            cost = summary["estimated_cost_usd"]
            rate = summary["output_tokens_per_second"]["p50"]
            rows.append(
                [
                    model["name"],
                    f"{summary['success_rate']:.0%}",
                    format_seconds(summary["latency_seconds"]["p50"]),
                    format_seconds(summary["latency_seconds"]["p95"]),
                    format_seconds(summary["ttft_seconds"]["p50"]),
                    "n/a" if rate is None else f"{rate:.1f}",
                    "n/a" if cost is None else f"${cost:.6f}",
                ]
            )
            statuses.append(summary["success_rate"])

    colors = None
    if color:
        colors = [
            "\x1b[32m" if status == 1 else "\x1b[33m" if status > 0 else "\x1b[31m"
            for status in statuses
        ]
    lines = [
        f"\x1b[1m{result['benchmark']}\x1b[0m" if color else result["benchmark"],
        f"Run {result['run_id']}  •  {result['timestamp']}",
        *([f"Prompt {result['prompt_name']}"] if result.get("prompt_name") else []),
        "",
        *_terminal_table(headers, rows, colors),
        "",
        "\x1b[1;36mExecutive summary\x1b[0m" if color else "Executive summary",
    ]
    for line in _executive_summary(result)[3:]:
        if not line:
            lines.append("")
            continue
        lines.append(line.replace("**", ""))
    return "\n".join(lines) + "\n"
