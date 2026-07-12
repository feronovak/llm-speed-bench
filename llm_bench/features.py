from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from .catalog import resolve_models
from .client import PROVIDER_DEFAULTS
from .presets import SUPPORTED_PRESETS, expand_presets
from .runner import _profile_request_count


def apply_smoke_mode(config: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(config)
    updated["repetitions"] = 1
    updated["suite_repetitions"] = 1
    updated["warmups"] = 0
    updated["concurrency"] = 1
    name = updated.get("name", "llm-benchmark")
    updated["name"] = name if name.endswith("-smoke") else f"{name}-smoke"
    return updated


def apply_environment(config: dict[str, Any], name: str | None) -> dict[str, Any]:
    if not name:
        return config
    environments = config.get("environments", {})
    if name not in environments:
        raise ValueError(f"unknown environment {name!r}")
    updated = copy.deepcopy(config)
    overlay = environments[name]
    for key, value in overlay.items():
        updated[key] = copy.deepcopy(value)
    return updated


def apply_model_aliases(config: dict[str, Any]) -> dict[str, Any]:
    aliases = config.get("aliases", {})
    if not aliases:
        return config
    updated = copy.deepcopy(config)
    resolved = []
    for model in updated.get("models", []):
        if isinstance(model, str):
            if model not in aliases:
                raise ValueError(f"unknown model alias {model!r}")
            resolved.append(copy.deepcopy(aliases[model]))
        else:
            resolved.append(model)
    updated["models"] = resolved
    return updated


def apply_provider_presets(config: dict[str, Any]) -> dict[str, Any]:
    presets = [str(preset) for preset in config.get("presets", [])]
    if not presets:
        return config
    unknown = sorted(set(presets) - SUPPORTED_PRESETS)
    if unknown:
        raise ValueError(f"unknown presets: {', '.join(unknown)}")

    updated = copy.deepcopy(config)
    updated["request"] = expand_presets(updated.get("request", {}), presets)
    return updated


def filter_changed_models(
    models: list[dict[str, Any]], previous: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    seen = {
        (model.get("provider", "openai_compatible"), model["model"])
        for model in previous
    }
    return [
        model
        for model in models
        if (model.get("provider", "openai_compatible"), model["model"]) not in seen
    ]


def _request_count(config: dict[str, Any], models: list[dict[str, Any]]) -> int:
    profile_selector = config.get("profiles")
    warmups = int(config["warmups"]) if "warmups" in config else 1
    if profile_selector:
        from .runner import select_test_profiles

        profiles = select_test_profiles(config, str(profile_selector))
        per_model = _profile_request_count(
            profiles,
            int(config.get("suite_repetitions", 1)),
        )
        warmups_per_model = warmups * len(profiles)
    else:
        per_model = int(config.get("repetitions", 5))
        warmups_per_model = warmups
    return len(models) * (per_model + warmups_per_model)


def estimate_budget(config: dict[str, Any]) -> dict[str, Any]:
    models = resolve_models(config)
    requests = _request_count(config, models)
    prompt_chars = len(config.get("prompt", ""))
    estimated_input_tokens = max(1, prompt_chars // 4)
    max_output_tokens = int(
        config.get("request", {}).get("max_output_tokens")
        or config.get("request", {}).get("max_tokens")
        or 256
    )
    costs = []
    for model in models:
        input_price = model.get("input_cost_per_million")
        output_price = model.get("output_cost_per_million")
        if input_price is None or output_price is None:
            costs.append(None)
            continue
        per_request = (
            estimated_input_tokens * float(input_price)
            + max_output_tokens * float(output_price)
        ) / 1_000_000
        costs.append(per_request)
    cost = (
        None
        if any(item is None for item in costs)
        else sum(costs) * (requests / len(models) if models else 0)
    )
    return {"requests": requests, "estimated_cost_usd": cost}


def check_budget(config: dict[str, Any]) -> dict[str, Any]:
    budget = estimate_budget(config)
    max_requests = config.get("max_requests")
    if max_requests is not None and budget["requests"] > int(max_requests):
        raise ValueError(
            f"estimated requests {budget['requests']} exceed max_requests {max_requests}"
        )
    max_cost = config.get("max_estimated_cost_usd")
    cost = budget["estimated_cost_usd"]
    if max_cost is not None and cost is not None and cost > float(max_cost):
        raise ValueError(
            f"estimated cost ${cost:.6f} exceeds max_estimated_cost_usd ${float(max_cost):.6f}"
        )
    return budget


def doctor_report(config: dict[str, Any]) -> dict[str, Any]:
    checks = []
    try:
        models = resolve_models(config)
    except ValueError as exc:
        return {
            "ok": False,
            "models": 0,
            "checks": [{"ok": False, "message": str(exc)}],
        }
    for model in models:
        provider = model.get("provider", "openai_compatible")
        resolved = {**PROVIDER_DEFAULTS.get(provider, {}), **model}
        key_env = resolved.get("api_key_env")
        if key_env and not os.environ.get(key_env):
            checks.append(
                {
                    "ok": False,
                    "model": model["model"],
                    "message": f"environment variable {key_env} is not set",
                }
            )
        elif "base_url" not in resolved:
            checks.append(
                {
                    "ok": False,
                    "model": model["model"],
                    "message": "base_url is required",
                }
            )
        else:
            checks.append(
                {
                    "ok": True,
                    "model": model["model"],
                    "message": "configuration looks runnable",
                }
            )
    return {
        "ok": all(check["ok"] for check in checks),
        "models": len(models),
        "checks": checks,
    }


def _metric(summary: dict[str, Any], path: tuple[str, ...]) -> float | None:
    value: Any = summary
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def compare_results(
    baseline: dict[str, Any],
    current: dict[str, Any],
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or {"latency_p95": 0.25, "success_rate": -0.05}
    previous = {
        model.get("name", model.get("model")): model for model in baseline["models"]
    }
    rows = []
    for model in current["models"]:
        name = model.get("name", model.get("model"))
        if name not in previous:
            rows.append({"name": name, "status": "added", "regressions": []})
            continue
        old_summary = previous[name]["summary"]
        new_summary = model["summary"]
        old_latency = _metric(old_summary, ("latency_seconds", "p95"))
        new_latency = _metric(new_summary, ("latency_seconds", "p95"))
        old_cost = old_summary.get("estimated_cost_usd")
        new_cost = new_summary.get("estimated_cost_usd")
        success_delta = new_summary.get("success_rate", 0) - old_summary.get(
            "success_rate", 0
        )
        latency_delta = (
            None
            if old_latency is None or new_latency is None
            else new_latency - old_latency
        )
        cost_delta = (
            None if old_cost is None or new_cost is None else new_cost - old_cost
        )
        regressions = []
        if (
            latency_delta is not None
            and old_latency
            and latency_delta / old_latency > thresholds.get("latency_p95", 1)
        ):
            regressions.append("latency_p95")
        if success_delta < thresholds.get("success_rate", -1):
            regressions.append("success_rate")
        rows.append(
            {
                "name": name,
                "status": "compared",
                "latency_p95_delta_seconds": latency_delta,
                "cost_delta_usd": cost_delta,
                "success_rate_delta": success_delta,
                "regressions": regressions,
            }
        )
    return {"ok": not any(row["regressions"] for row in rows), "models": rows}


def replay_config(result: dict[str, Any]) -> dict[str, Any]:
    if "source_config" not in result:
        raise ValueError("result does not include source_config; cannot replay exactly")
    config = copy.deepcopy(result["source_config"])
    replay_model_keys = (
        "provider",
        "model",
        "name",
        "base_url",
        "api_key_env",
        "api_version",
        "max_tokens_parameter",
        "capabilities",
        "supports_temperature",
    )
    config["models"] = [
        {key: model[key] for key in replay_model_keys if key in model}
        for model in result["models"]
    ]
    config["discovery"] = []
    settings = result.get("settings", {})
    for key in ("repetitions", "warmups", "concurrency", "suite_repetitions"):
        if key in settings:
            config[key] = settings[key]
    if settings.get("request"):
        config["request"] = settings["request"]
    return config


def matrix_report(result: dict[str, Any]) -> str:
    profile_names = []
    for model in result["models"]:
        for profile in model.get("profiles", []):
            if profile["name"] not in profile_names:
                profile_names.append(profile["name"])
    lines = [
        "| Model | " + " | ".join(profile_names) + " |",
        "|---|" + "|".join("---:" for _ in profile_names) + "|",
    ]
    for model in result["models"]:
        by_name = {profile["name"]: profile for profile in model.get("profiles", [])}
        values = []
        for name in profile_names:
            profile = by_name.get(name)
            if not profile:
                values.append("n/a")
            else:
                rate = profile["summary"].get(
                    "valid_output_rate", profile["summary"].get("success_rate", 0)
                )
                values.append(f"{rate:.0%}")
        lines.append(f"| {model['name']} | " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
