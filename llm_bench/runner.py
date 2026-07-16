from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import uuid
from contextlib import contextmanager
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import resolve_models
from .client import create_client
from .metrics import summarize
from .presets import expand_presets
from .pricing import pricing_freshness_report
from .profiles import (
    PROFILE_ALIASES,
    evaluate_response,
    normalize_profile_selector,
    select_profiles,
)
from .redaction import redact_secrets


@contextmanager
def benchmark_run_lock(output_dir: Path):
    """Prevent two processes spending money on the same result directory."""
    import fcntl

    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = output_dir / ".llm-bench.run.lock"
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                f"a benchmark is already running for {output_dir}; wait for it to finish"
            ) from exc
        yield
    finally:
        os.close(descriptor)


def _should_keep_response(config: dict[str, Any], sample: dict[str, Any]) -> bool:
    setting = config.get("save_responses", False)
    if setting is True:
        return True
    if setting == "failures":
        return not sample["ok"] or sample.get("valid_output") is False
    if isinstance(setting, int):
        return setting > 0
    return False


def model_failed(model: dict[str, Any]) -> bool:
    return model_has_api_error(model) or model_has_test_failure(model)


def model_has_api_error(model: dict[str, Any]) -> bool:
    profiles = model.get("profiles", [])
    if profiles:
        return any(profile["summary"].get("failed", 0) for profile in profiles)
    return bool(model.get("summary", {}).get("failed", 0))


def model_has_test_failure(model: dict[str, Any]) -> bool:
    profiles = model.get("profiles", [])
    if profiles:
        return any(
            profile["summary"].get("valid_output_rate", 1) < 1 for profile in profiles
        )
    return any(
        sample.get("valid_output") is False for sample in model.get("samples", [])
    )


def result_failed(result: dict[str, Any]) -> bool:
    return any(model_failed(model) for model in result["models"])


def _invalid_output_count(samples: list[dict[str, Any]]) -> int:
    return sum(sample.get("valid_output") is False for sample in samples)


def _should_stop(config: dict[str, Any], model_result: dict[str, Any]) -> bool:
    stop_on = config.get("stop_on")
    if config.get("fail_fast") and stop_on is None:
        stop_on = "any-fail"
    if stop_on == "api-error":
        return model_has_api_error(model_result)
    if stop_on == "test-fail":
        return model_has_test_failure(model_result)
    if stop_on == "any-fail":
        return model_failed(model_result)
    return False


def _add_response_preview(sample: dict[str, Any], limit: int = 240) -> None:
    response = sample.get("response")
    if (sample["ok"] and sample.get("valid_output") is not False) or not response:
        return
    preview = " ".join(str(response).split())
    sample["response_preview"] = preview[:limit]


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    config_dir = path.parent.resolve()
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
        if "prompt_file" in prompt:
            if "prompt" in prompt:
                raise ValueError(
                    f"prompts[{index}] must use either 'prompt' or 'prompt_file'"
                )
            prompt_file = prompt["prompt_file"]
            if not isinstance(prompt_file, str) or not prompt_file:
                raise ValueError(f"prompts[{index}] requires a non-empty 'prompt_file'")
            prompt_path = Path(prompt_file)
            if prompt_path.is_absolute():
                raise ValueError(f"prompts[{index}].prompt_file must be relative")
            resolved_prompt_path = (config_dir / prompt_path).resolve()
            if config_dir != resolved_prompt_path and config_dir not in (
                resolved_prompt_path.parents
            ):
                raise ValueError(
                    f"prompts[{index}].prompt_file must stay within the config directory"
                )
            if not resolved_prompt_path.is_file():
                raise ValueError(
                    f"prompts[{index}].prompt_file does not exist or is not a file"
                )
            prompt["prompt"] = resolved_prompt_path.read_text(encoding="utf-8")
        if not isinstance(prompt.get("prompt"), str) or not prompt["prompt"]:
            raise ValueError(f"prompts[{index}] requires a non-empty 'prompt'")
        prompt_names.append(prompt["name"])
    if len(prompt_names) != len(set(prompt_names)):
        raise ValueError("custom prompt names must be unique")
    if not config.get("models") and not config.get("discovery"):
        raise ValueError("config requires 'models' or 'discovery'")
    aliases = config.get("aliases", {})
    for index, model in enumerate(config.get("models", [])):
        if isinstance(model, str):
            if model not in aliases:
                raise ValueError(f"models[{index}] references unknown alias {model!r}")
            continue
        if not isinstance(model, dict) or "model" not in model:
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


def _validation_evaluator(validation: dict[str, Any]) -> dict[str, Any]:
    if "json_schema" in validation:
        return {"type": "json_schema", "schema": validation["json_schema"]}
    if "regex" in validation:
        return {"type": "regex", "regex": validation["regex"]}
    if "contains" in validation:
        return {"type": "contains", "contains": validation["contains"]}
    if "exact" in validation:
        return {"type": "exact", "expected": validation["exact"]}
    return {"type": "nonempty"}


def custom_prompt_profile(prompt: dict[str, Any]) -> dict[str, Any]:
    profile = {
        "name": prompt["name"],
        "description": prompt.get("description", "Custom prompt test."),
        "cases": [
            {
                "id": prompt["name"],
                "prompt": prompt["prompt"],
                "evaluator": _validation_evaluator(prompt.get("validation", {})),
            }
        ],
    }
    if "request" in prompt:
        profile["request"] = dict(prompt["request"])
    if "presets" in prompt:
        profile["presets"] = list(prompt["presets"])
    if prompt.get("system_prompt"):
        profile["system_prompt"] = prompt["system_prompt"]
    return profile


def select_test_profiles(config: dict[str, Any], selector: str) -> list[dict[str, Any]]:
    requested = normalize_profile_selector(selector).split(",")
    builtins_all = select_profiles("all")
    builtin_names = [profile["name"] for profile in builtins_all]
    prompt_names = [prompt["name"] for prompt in config.get("prompts", [])]
    duplicates = sorted({name for name in prompt_names if prompt_names.count(name) > 1})
    collisions = sorted(set(prompt_names) & set(builtin_names))
    if duplicates:
        raise ValueError(f"duplicate custom prompt names: {', '.join(duplicates)}")
    if collisions:
        raise ValueError(
            f"custom prompt names collide with built-in profiles: {', '.join(collisions)}"
        )
    custom_profiles = {
        prompt["name"]: custom_prompt_profile(prompt)
        for prompt in config.get("prompts", [])
    }
    if requested == ["all"]:
        return builtins_all
    unknown = sorted(set(requested) - set(builtin_names) - set(custom_profiles))
    if unknown:
        available = ", ".join([*builtin_names, *custom_profiles])
        raise ValueError(
            f"unknown profiles: {', '.join(unknown)}; choose all or {available}"
        )
    builtins = [profile for profile in builtins_all if profile["name"] in requested]
    customs = [custom_profiles[name] for name in requested if name in custom_profiles]
    return [*builtins, *customs]


def _validate(sample: dict[str, Any], validation: dict[str, Any]) -> None:
    if not sample["ok"]:
        sample["valid_output"] = False
        sample["evaluation_error"] = "request failed"
        return
    response = sample["response"]
    sample["valid_output"] = True
    sample["evaluation_error"] = None
    if "contains" in validation and (
        not validation["contains"] or validation["contains"] not in response
    ):
        sample["valid_output"] = False
        sample["evaluation_error"] = (
            f"response did not contain {validation['contains']!r}"
        )
    if "regex" in validation and not re.search(validation["regex"], response):
        sample["valid_output"] = False
        sample["evaluation_error"] = (
            f"response did not match regex {validation['regex']!r}"
        )
    if "exact" in validation and (
        response.strip().casefold() != str(validation["exact"]).strip().casefold()
    ):
        sample["valid_output"] = False
        sample["evaluation_error"] = "exact match failed"
    if "json_schema" in validation:
        evaluation = evaluate_response(
            response, {"type": "json_schema", "schema": validation["json_schema"]}
        )
        if not evaluation["valid"]:
            sample["valid_output"] = False
            sample["evaluation_error"] = evaluation["error"]
    _add_response_preview(sample)


def validate_config_validations(config: dict[str, Any]) -> None:
    """Reject unsupported validation keys before planning or spending requests."""

    def validate_rules(validation: Any, location: str) -> None:
        if validation is None:
            return
        if not isinstance(validation, dict):
            raise ValueError(f"{location} must be an object")
        allowed = {"contains", "regex", "json_schema", "exact"}
        unknown = sorted(set(validation) - allowed)
        if unknown:
            raise ValueError(f"unknown validation keys: {', '.join(unknown)}")
        if "contains" in validation and (
            not isinstance(validation["contains"], str) or not validation["contains"]
        ):
            raise ValueError(f"{location}.contains must be a non-empty string")

    validate_rules(config.get("validation", {}), "validation")
    for prompt_config in config.get("prompts", []):
        validate_rules(
            prompt_config.get("validation", {}),
            f"validation for prompt {prompt_config.get('name', '<unnamed>')}",
        )
    prompt_names = [prompt.get("name") for prompt in config.get("prompts", [])]
    duplicates = sorted(
        {
            str(name)
            for name in prompt_names
            if name is not None and prompt_names.count(name) > 1
        }
    )
    if duplicates:
        raise ValueError(f"duplicate custom prompt names: {', '.join(duplicates)}")
    builtin_names = {profile["name"] for profile in select_profiles("all")}
    builtin_names.update(PROFILE_ALIASES)
    collisions = sorted(str(name) for name in prompt_names if name in builtin_names)
    if collisions:
        raise ValueError(
            "custom prompt names collide with built-in profiles: "
            + ", ".join(collisions)
        )


def _request_exception_sample(exc: Exception) -> dict[str, Any]:
    error = str(exc)
    category = (
        "network"
        if any(
            token in error.casefold()
            for token in ("resolve", "network", "connection", "timeout")
        )
        else "provider_error"
    )
    return {
        "ok": False,
        "latency_seconds": 0.0,
        "ttft_seconds": None,
        "output_tokens_per_second": None,
        "input_tokens": None,
        "output_tokens": None,
        "response_chars": 0,
        "response": "",
        "error": error,
        "attempts": 1,
        "retry_count": 0,
        "retry_reasons": [],
        "failure_category": category,
    }


def _safe_client_run(
    client: Any, prompt: str, options: dict[str, Any]
) -> dict[str, Any]:
    try:
        return client.run(prompt, options)
    except Exception as exc:
        return _request_exception_sample(exc)


class _UnavailableClient:
    def __init__(self, model: dict[str, Any], error: Exception):
        self.model = {"base_url": model.get("base_url")}
        self.error = error

    def run(self, _prompt: str, _options: dict[str, Any]) -> dict[str, Any]:
        return _request_exception_sample(self.error)


def _execute(
    client: Any,
    jobs: list[tuple[dict[str, Any], dict[str, Any], int | None]],
    concurrency: int,
    save_responses: bool | str,
    on_complete: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(_safe_client_run, client, case["prompt"], options): (
                case,
                level,
            )
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
            _add_response_preview(sample)
            if level is not None:
                sample["concurrency"] = level
            failed_output = not sample["ok"] or not sample.get("valid_output", True)
            if save_responses != "failures" and not save_responses:
                sample.pop("response", None)
            elif save_responses == "failures" and not failed_output:
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
        suffix = (
            f"@c{sample['concurrency']}"
            if sample.get("concurrency") is not None
            else ""
        )
        callback(sample, f"{profile_name}/{case['id']}{suffix}")

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
    save_responses = config.get("save_responses", False)
    results = []
    for profile in profiles:
        progress_callback = _profile_progress_callback(on_complete, profile["name"])
        options = dict(request_options)
        profile_request = profile.get("request", {})
        if profile.get("presets"):
            profile_request = expand_presets(profile_request, profile["presets"])
        options.update(profile_request)
        if profile.get("system_prompt"):
            options["system_prompt"] = profile["system_prompt"]
        for _ in range(warmups):
            warmup_samples.append(
                _safe_client_run(client, profile["cases"][0]["prompt"], options)
            )

        if "concurrency_levels" in profile:
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
        if "concurrency_levels" in profile
        else len(profile["cases"]) * repetitions
        for profile in profiles
    )


def profile_request_breakdown(
    profiles: list[dict[str, Any]], repetitions: int
) -> list[dict[str, Any]]:
    breakdown = []
    for profile in profiles:
        if "concurrency_levels" in profile:
            levels = [
                (level, max(repetitions, level))
                for level in profile["concurrency_levels"]
            ]
            breakdown.append(
                {
                    "name": profile["name"],
                    "requests_per_model": sum(count for _, count in levels),
                    "details": "load levels: "
                    + ", ".join(f"c{level}={count}" for level, count in levels),
                }
            )
        else:
            count = len(profile["cases"]) * repetitions
            breakdown.append(
                {
                    "name": profile["name"],
                    "requests_per_model": count,
                    "details": f"{len(profile['cases'])} cases x {repetitions} repetitions",
                }
            )
    return breakdown


def run_benchmark(
    config: dict[str, Any],
    profile_selector: str | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    validate_config_validations(config)
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
    profiles = (
        select_test_profiles(config, profile_selector) if profile_selector else []
    )
    if "prompt" not in config and not profiles:
        raise ValueError("select a custom prompt before running the benchmark")
    prompt = config.get("prompt", "")
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
        client: Any
        try:
            client = create_client(model, timeout)
        except (OSError, ValueError) as exc:
            client = _UnavailableClient(model, exc)
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
                        "error": redact_secrets(sample.get("error")),
                        "valid_output": sample.get("valid_output"),
                        "evaluation_error": redact_secrets(
                            sample.get("evaluation_error")
                        ),
                        "response_preview": redact_secrets(
                            sample.get("response_preview")
                        ),
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
                warmup_samples.append(_safe_client_run(client, prompt, request_options))
            samples = []
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(_safe_client_run, client, prompt, request_options)
                    for _ in range(repetitions)
                ]
                for future in as_completed(futures):
                    sample = future.result()
                    _validate(sample, validation)
                    report_sample(sample, config.get("prompt_name", "config-prompt"))
                    if not _should_keep_response(config, sample):
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
                    "invalid_outputs": _invalid_output_count(samples),
                }
            )
        if _should_stop(config, model_result):
            break

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
        "pricing_warnings": pricing_freshness_report(models)["warnings"],
        "source_config": redact_secrets(config),
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
    return redact_secrets(result)


def save_result(result: dict[str, Any], output_dir: Path) -> Path:
    result = redact_secrets(result)
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    stamp = result["timestamp"].replace(":", "").replace("+00:00", "Z")
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", result["benchmark"]).strip("-")
    path = output_dir / f"{stamp}-{name}-{result['run_id'][:8]}.json"
    _write_private(path, json.dumps(result, indent=2) + "\n")
    _write_private(path.with_suffix(".md"), report(result))
    _write_private(
        path.with_suffix(".summary.md"),
        "\n".join(_executive_summary(result)).strip() + "\n",
    )
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
        reliability = summary.get("valid_output_rate", summary.get("success_rate", 0))
        qualified = reliability == 1 and summary.get("failed", 0) == 0
        return {
            "name": model.get("name", model.get("model", "unknown")),
            "requests": summary.get("requests", 0),
            "reliability": reliability,
            "latency": summary.get("latency_seconds", {}).get("mean"),
            "cost": summary.get("estimated_cost_usd"),
            "qualified": qualified,
            "failed_tests": [] if qualified else ["config prompt"],
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
    failed_tests = [
        profile["name"]
        for profile in profiles
        if profile["summary"].get("failed", 0)
        or profile["summary"].get(
            "valid_output_rate", profile["summary"].get("success_rate", 0)
        )
        < 1
    ]
    return {
        "name": model.get("name", model.get("model", "unknown")),
        "requests": requests,
        "reliability": valid / requests if requests else 0,
        "latency": weighted_latency / requests if requests else None,
        "cost": sum(costs) if all(cost is not None for cost in costs) else None,
        "qualified": not failed_tests,
        "failed_tests": failed_tests,
    }


def _executive_summary(result: dict[str, Any]) -> list[str]:
    metrics = [_ranking_metrics(model) for model in result["models"]]
    timed = [
        item
        for item in metrics
        if item["qualified"] and item["latency"] is not None and item["requests"]
    ]
    priced = [
        item
        for item in metrics
        if item["qualified"] and item["cost"] is not None and item["requests"]
    ]
    lines = ["", "## Executive summary", ""]
    if timed:
        fastest = min(timed, key=lambda item: item["latency"])
        lines.append(
            f"- Fastest: **{fastest['name']}** — "
            f"{fastest['latency']:.3f}s mean latency."
        )
    else:
        lines.append("- Fastest: unavailable; no model passed every selected test.")
    if priced:
        cheapest = min(priced, key=lambda item: item["cost"])
        lines.append(
            f"- Cheapest: **{cheapest['name']}** — ${cheapest['cost']:.6f} total."
        )
    else:
        lines.append(
            "- Cheapest: unavailable; no priced model passed every selected test."
        )
    value_candidates = [item for item in priced if item["latency"] is not None]
    if value_candidates:
        min_latency = min(item["latency"] for item in value_candidates)
        min_cost = min(item["cost"] for item in value_candidates)
        for item in value_candidates:
            speed = (
                1.0
                if item["latency"] == 0
                else min_latency / item["latency"]
                if min_latency > 0
                else 0.0
            )
            cost = min_cost / item["cost"] if item["cost"] else 1.0
            item["value_score"] = (item["reliability"] + speed + cost) / 3
        best = max(value_candidates, key=lambda item: item["value_score"])
        lines.append(
            f"- Best value: **{best['name']}** — "
            f"{best['value_score']:.0%} composite score."
        )
        lines.append(
            f"- Recommended: **{best['name']}** — passed every selected test and "
            "led the qualified value ranking."
        )
    else:
        lines.append(
            "- Best value: unavailable; no priced model passed every selected test."
        )
        lines.append(
            "- Recommended: unavailable; no priced model passed every selected test."
        )
    excluded = [item for item in metrics if item["requests"] and not item["qualified"]]
    if excluded:
        details = "; ".join(
            f"**{item['name']}** (failed: {', '.join(item['failed_tests'])})"
            for item in excluded
        )
        lines.append(f"- Excluded from recommendations: {details}.")
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
            "Value equally weights reliability, relative speed, and relative cost "
            "among models that passed every selected test.",
        ]
    )
    return lines


def _failed_tests(model: dict[str, Any]) -> str:
    failed_profiles = [
        profile["name"]
        for profile in model.get("profiles", [])
        if profile["summary"].get("failed", 0)
        or profile["summary"].get("valid_output_rate", 1) < 1
    ]
    if failed_profiles:
        return ", ".join(failed_profiles)
    failure_reasons = model.get("summary", {}).get("failure_reasons", {})
    if failure_reasons:
        return ", ".join(failure_reasons)
    if model.get("summary", {}).get("failed", 0):
        return "request failed"
    if model_has_test_failure(model):
        return "config prompt"
    return "-"


def _model_passed(model: dict[str, Any]) -> bool:
    return not model_failed(model)


def _pass_fail_rows(result: dict[str, Any]) -> list[list[str]]:
    rows = []
    for model in result["models"]:
        passed = _model_passed(model)
        rows.append(
            [
                model.get("name", model.get("model", "unknown")),
                "PASS" if passed else "FAIL",
                "-" if passed else _failed_tests(model),
            ]
        )
    return rows


def _markdown_pass_fail_dashboard(result: dict[str, Any]) -> list[str]:
    rows = _pass_fail_rows(result)
    lines = [
        "## Pass/fail dashboard",
        "",
        "| Model | Result | Failed tests |",
        "|---|---|---|",
    ]
    lines.extend(f"| {row[0]} | {row[1]} | {row[2]} |" for row in rows)
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
    if result.get("pricing_warnings"):
        lines.extend(["", "## Pricing warnings", ""])
        for warning in result["pricing_warnings"]:
            lines.append(
                f"- {warning['provider']}/{warning['model']}: {warning['message']}"
            )
    lines.extend(["", *_markdown_pass_fail_dashboard(result)])
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
            "Valid",
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
                    f"{summary.get('valid_output_rate', summary['success_rate']):.0%}",
                    format_seconds(summary["latency_seconds"]["p50"]),
                    format_seconds(summary["latency_seconds"]["p95"]),
                    format_seconds(summary["ttft_seconds"]["p50"]),
                    "n/a" if rate is None else f"{rate:.1f}",
                    "n/a" if cost is None else f"${cost:.6f}",
                ]
            )
            statuses.append(summary.get("valid_output_rate", summary["success_rate"]))

    colors = None
    if color:
        colors = [
            "\x1b[32m" if status == 1 else "\x1b[33m" if status > 0 else "\x1b[31m"
            for status in statuses
        ]

    def section(title: str) -> str:
        text = f"=== {title} ==="
        return f"\x1b[1;36m{text}\x1b[0m" if color else text

    lines = [
        f"\x1b[1m{result['benchmark']}\x1b[0m" if color else result["benchmark"],
        f"Run {result['run_id']}  •  {result['timestamp']}",
        *([f"Prompt {result['prompt_name']}"] if result.get("prompt_name") else []),
        "",
        section("RESULTS"),
        *_terminal_table(headers, rows, colors),
        "",
        section("QUALITY GATE"),
        "Pass/fail dashboard",
        *_terminal_table(
            ["Model", "Result", "Failed tests"],
            _pass_fail_rows(result),
            [
                "\x1b[32m" if row[1] == "PASS" else "\x1b[31m"
                for row in _pass_fail_rows(result)
            ]
            if color
            else None,
        ),
        "",
        section("DECISION"),
        "Executive summary",
    ]
    for line in _executive_summary(result)[3:]:
        if not line:
            lines.append("")
            continue
        lines.append(line.replace("**", ""))
    return "\n".join(lines) + "\n"
