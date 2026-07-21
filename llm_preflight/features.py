from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, cast

from .catalog import resolve_models
from .client import PROVIDER_DEFAULTS
from .pricing import pricing_freshness_report
from .pricing import estimate_sample_cost
from .presets import SUPPORTED_PRESETS, expand_presets
from .runner import select_test_profiles


def apply_smoke_mode(config: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(config)
    updated["repetitions"] = 1
    updated["suite_repetitions"] = 1
    updated["warmups"] = 0
    updated["concurrency"] = 1
    name = updated.get("name", "llm-benchmark")
    updated["name"] = name if name.endswith("-smoke") else f"{name}-smoke"
    return updated


def apply_migration_check(config: dict[str, Any]) -> dict[str, Any]:
    """Configure one cheap, comparable response-contract preflight."""
    updated = copy.deepcopy(config)
    updated["profiles"] = "quick-migration-check"
    updated["suite_repetitions"] = 1
    updated["warmups"] = 0
    updated["concurrency"] = 1
    name = updated.get("name", "llm-benchmark")
    updated["name"] = (
        name if name.endswith("-migration-check") else f"{name}-migration-check"
    )
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


def _retry_max_attempts(request: dict[str, Any]) -> int:
    retry = request.get("retry", {})
    if retry is True:
        retry = {}
    if not isinstance(retry, dict):
        return 2
    return max(1, int(retry.get("max_attempts", 2)))


def _budget_work(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return every nominal request shape, including warmups, for cost safety."""
    request = dict(config.get("request", {}))
    warmups = int(config.get("warmups", 1))
    selector = config.get("profiles")
    if not selector:
        return [(config.get("prompt", ""), request)] * (
            int(config.get("repetitions", 5)) + warmups
        )

    work: list[tuple[str, dict[str, Any]]] = []
    repetitions = int(config.get("suite_repetitions", 1))
    for profile in select_test_profiles(config, str(selector)):
        options = dict(request)
        profile_request = dict(profile.get("request", {}))
        if profile.get("presets"):
            profile_request = expand_presets(profile_request, profile["presets"])
        options.update(profile_request)
        if profile.get("system_prompt"):
            options["system_prompt"] = profile["system_prompt"]
        work.extend([(profile["cases"][0]["prompt"], options)] * warmups)
        if "concurrency_levels" in profile:
            for level in profile["concurrency_levels"]:
                work.extend(
                    (
                        profile["cases"][index % len(profile["cases"])]["prompt"],
                        options,
                    )
                    for index in range(max(repetitions, level))
                )
        else:
            work.extend(
                (case["prompt"], options)
                for case in profile["cases"]
                for _ in range(repetitions)
            )
    return work


def _request_cost(
    model: dict[str, Any], prompt: str, options: dict[str, Any]
) -> float | None:
    system_prompt = options.get("system_prompt", "")
    system_text = (
        system_prompt
        if isinstance(system_prompt, str)
        else json.dumps(system_prompt, separators=(",", ":"), sort_keys=True)
    )
    input_chars = len(prompt) + len(system_text)
    input_tokens = max(1, input_chars // 4)
    max_output_tokens = int(
        options.get("max_output_tokens") or options.get("max_tokens") or 256
    )
    return estimate_sample_cost(
        {"input_tokens": input_tokens, "output_tokens": max_output_tokens}, model
    )


def estimate_budget(config: dict[str, Any]) -> dict[str, Any]:
    models = resolve_models(config)
    work = _budget_work(config)
    requests = len(work) * len(models)
    possible_requests = len(models) * sum(
        _retry_max_attempts(options) for _, options in work
    )
    retry_max_attempts = max(
        (_retry_max_attempts(options) for _, options in work), default=1
    )
    costs: list[float | None] = []
    maximum_costs: list[float | None] = []
    for model in models:
        request_costs = [
            _request_cost(model, prompt, options) for prompt, options in work
        ]
        if any(cost is None for cost in request_costs):
            costs.append(None)
            maximum_costs.append(None)
            continue
        known_costs = [cast(float, cost) for cost in request_costs if cost is not None]
        costs.append(sum(known_costs))
        maximum_costs.append(
            sum(
                cast(float, cost) * _retry_max_attempts(options)
                for cost, (_, options) in zip(request_costs, work)
                if cost is not None
            )
        )
    cost = (
        None
        if any(item is None for item in costs)
        else sum(cast(float, item) for item in costs)
    )
    maximum_cost = (
        None
        if any(item is None for item in maximum_costs)
        else sum(cast(float, item) for item in maximum_costs)
    )
    return {
        "requests": requests,
        "possible_requests": possible_requests,
        "retry_max_attempts": retry_max_attempts,
        "estimated_cost_usd": cost,
        "maximum_estimated_cost_usd": (maximum_cost),
    }


def check_budget(config: dict[str, Any]) -> dict[str, Any]:
    budget = estimate_budget(config)
    max_requests = config.get("max_requests")
    if max_requests is not None and budget["possible_requests"] > int(max_requests):
        raise ValueError(
            f"possible requests {budget['possible_requests']} exceed max_requests {max_requests}"
        )
    max_cost = config.get("max_estimated_cost_usd")
    cost = budget["maximum_estimated_cost_usd"]
    if max_cost is not None:
        if cost is None:
            raise ValueError(
                "pricing is unknown; cannot enforce max_estimated_cost_usd"
            )
        if cost > float(max_cost):
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
    for warning in pricing_freshness_report(models)["warnings"]:
        checks.append(
            {
                "ok": True,
                "severity": warning["severity"],
                "model": warning["model"],
                "message": warning["message"],
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
    defaults = {
        "latency_p95": 0.25,
        "success_rate": -0.05,
        "valid_output_rate": -0.05,
        "cost": 0.25,
    }
    thresholds = {**defaults, **(thresholds or {})}
    previous = {
        model.get("name", model.get("model")): model for model in baseline["models"]
    }
    rows = []
    current_names = {
        model.get("name", model.get("model")) for model in current["models"]
    }
    for name in previous.keys() - current_names:
        rows.append({"name": name, "status": "removed", "regressions": ["removed"]})
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
        old_valid_output = old_summary.get("valid_output_rate")
        new_valid_output = new_summary.get("valid_output_rate")
        valid_output_delta = (
            None
            if old_valid_output is None or new_valid_output is None
            else new_valid_output - old_valid_output
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
        if latency_delta is not None and (
            (old_latency == 0 and latency_delta > thresholds.get("latency_p95", 0))
            or (
                old_latency is not None
                and old_latency > 0
                and latency_delta / old_latency > thresholds.get("latency_p95", 1)
            )
        ):
            regressions.append("latency_p95")
        if success_delta < thresholds.get("success_rate", -1):
            regressions.append("success_rate")
        if (
            valid_output_delta is None
            or valid_output_delta < thresholds["valid_output_rate"]
        ):
            regressions.append("valid_output_rate")
        if cost_delta is not None and (
            (old_cost == 0 and cost_delta > thresholds.get("cost", 0))
            or (
                old_cost not in {None, 0}
                and cost_delta / old_cost > thresholds.get("cost", float("inf"))
            )
        ):
            regressions.append("cost")
        rows.append(
            {
                "name": name,
                "status": "compared",
                "latency_p95_delta_seconds": latency_delta,
                "cost_delta_usd": cost_delta,
                "success_rate_delta": success_delta,
                "valid_output_rate_delta": valid_output_delta,
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
