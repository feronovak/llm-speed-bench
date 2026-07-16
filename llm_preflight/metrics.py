from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any


def percentile(values: Iterable[float], p: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def stats(values: Iterable[float]) -> dict[str, float | None]:
    data = list(values)
    if not data:
        return {"mean": None, "min": None, "p50": None, "p95": None, "max": None}
    return {
        "mean": sum(data) / len(data),
        "min": min(data),
        "p50": percentile(data, 0.50),
        "p95": percentile(data, 0.95),
        "max": max(data),
    }


def _failure_hints(samples: list[dict[str, Any]]) -> list[str]:
    hints: list[str] = []

    def add(hint: str) -> None:
        if hint not in hints:
            hints.append(hint)

    for sample in samples:
        failed_output = not sample["ok"] or sample.get("valid_output") is False
        if not failed_output:
            continue
        error = str(
            sample.get("error") or sample.get("evaluation_error") or ""
        ).casefold()
        preview = str(sample.get("response_preview") or "").strip()
        preview_folded = preview.casefold()
        output_tokens = sample.get("output_tokens")
        if preview.startswith("```"):
            add("response appears to be fenced Markdown instead of raw output")
        if (
            "did not match regex" in error or "invalid json" in error or "json" in error
        ) and (
            preview_folded.startswith("no markdown")
            or preview_folded.startswith("note:")
            or "i must output" in preview_folded
        ):
            add("reasoning or commentary appeared before the expected answer")
        if "unsupported" in error and "parameter" in error:
            add("provider rejected an unsupported request parameter")
        if "rate limit" in error or "rate limited" in error:
            add("provider rate limit or transient throttling occurred")
        if (
            output_tokens == 0
            and sample.get("input_tokens")
            and not preview
            and (not sample.get("error"))
        ):
            add("provider returned no visible content after a billable request")
    return hints


def summarize(samples: list[dict[str, Any]], model: dict[str, Any]) -> dict[str, Any]:
    successful = [sample for sample in samples if sample["ok"]]
    failure_reasons: dict[str, int] = {}
    retry_reasons: dict[str, int] = {}
    failure_categories: dict[str, int] = {}
    for sample in samples:
        for reason in sample.get("retry_reasons", []):
            retry_reasons[reason] = retry_reasons.get(reason, 0) + 1
        if sample["ok"]:
            continue
        reason = sample.get("error") or "unknown error"
        failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
        category = sample.get("failure_category")
        if category:
            failure_categories[category] = failure_categories.get(category, 0) + 1

    def successful_numbers(field: str) -> list[float]:
        return [
            float(sample[field])
            for sample in successful
            if sample.get(field) is not None
        ]

    def usage_numbers(field: str) -> list[float]:
        return [
            float(sample[field]) for sample in samples if sample.get(field) is not None
        ]

    input_tokens = sum(usage_numbers("input_tokens"))
    output_tokens = sum(usage_numbers("output_tokens"))
    input_price = model.get("input_cost_per_million")
    output_price = model.get("output_cost_per_million")
    cost = None
    if input_price is not None and output_price is not None:
        cost = (
            input_tokens * float(input_price) / 1_000_000
            + output_tokens * float(output_price) / 1_000_000
        )

    return {
        "requests": len(samples),
        "successful": len(successful),
        "failed": len(samples) - len(successful),
        "success_rate": len(successful) / len(samples) if samples else 0,
        "valid_output_rate": (
            sum(sample.get("valid_output", sample["ok"]) is True for sample in samples)
            / len(samples)
            if samples
            else 0
        ),
        "latency_seconds": stats(successful_numbers("latency_seconds")),
        "ttft_seconds": stats(successful_numbers("ttft_seconds")),
        "output_tokens_per_second": stats(
            successful_numbers("output_tokens_per_second")
        ),
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "estimated_cost_usd": cost,
        "failure_reasons": failure_reasons,
        "failure_categories": failure_categories,
        "retry_count": sum(int(sample.get("retry_count") or 0) for sample in samples),
        "retry_reasons": retry_reasons,
        "failure_hints": _failure_hints(samples),
    }
