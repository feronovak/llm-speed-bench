from __future__ import annotations

import json
import re
from typing import Any


BUILTIN_PROFILES: list[dict[str, Any]] = [
    {
        "name": "quick-migration-check",
        "description": (
            "API compatibility, basic response contract, TTFT, and latency."
        ),
        "cases": [
            {
                "id": "chat-capital",
                "prompt": "Answer in one short sentence: What is the capital of France?",
                "evaluator": {"type": "contains", "contains": "Paris"},
            },
            {
                "id": "chat-summary",
                "prompt": "Summarize in one sentence: A customer changed their email address and can no longer log in.",
                "evaluator": {"type": "contains", "contains": "email"},
            },
            {
                "id": "chat-rewrite",
                "prompt": "Rewrite politely in one sentence: Send the report today.",
                "evaluator": {"type": "regex", "regex": "(?i)please.*report.*today"},
            },
        ],
    },
    {
        "name": "exact-routing-check",
        "description": "Exact routing labels for downstream queues or actions.",
        "system_prompt": "Return only the requested lowercase label.",
        "cases": [
            {
                "id": "class-billing",
                "prompt": "Classify as billing, technical, or account: I was charged twice.",
                "evaluator": {"type": "exact", "expected": "billing"},
            },
            {
                "id": "class-technical",
                "prompt": "Classify as billing, technical, or account: The mobile app crashes on startup.",
                "evaluator": {"type": "exact", "expected": "technical"},
            },
            {
                "id": "class-account",
                "prompt": "Classify as billing, technical, or account: I need to change my login email.",
                "evaluator": {"type": "exact", "expected": "account"},
            },
        ],
    },
    {
        "name": "structured-output-check",
        "description": "JSON shape, required fields, and exact extracted values.",
        "system_prompt": "Return only valid JSON with no Markdown formatting.",
        "presets": ["structured"],
        "request": {"max_output_tokens": 512},
        "cases": [
            {
                "id": "extract-ticket",
                "prompt": (
                    "Extract product and priority as high, medium, or low; map "
                    '"Urgent" to high: "Urgent: payments are failing in Checkout."'
                ),
                "evaluator": {
                    "type": "json_subset",
                    "expected": {"priority": "high", "product": "Checkout"},
                },
            },
            {
                "id": "extract-person",
                "prompt": 'Extract name and city: "Marta Novak lives in Bratislava."',
                "evaluator": {
                    "type": "json_subset",
                    "expected": {"name": "Marta Novak", "city": "Bratislava"},
                },
            },
            {
                "id": "extract-order",
                "prompt": 'Extract order_id and quantity: "Order A-104 contains 7 units."',
                "evaluator": {
                    "type": "json_subset",
                    "expected": {"order_id": "A-104", "quantity": 7},
                },
            },
        ],
    },
    {
        "name": "numeric-instruction-check",
        "description": "Numeric task correctness and concise instruction following.",
        "system_prompt": "Return only the final numeric answer.",
        "cases": [
            {
                "id": "reason-percent",
                "prompt": (
                    "A price of 80 increases by 25%. What is the new price? "
                    "Return only the numeric answer. Do not include units, words, "
                    "or explanation."
                ),
                "evaluator": {
                    "type": "numeric_answer",
                    "expected": 100,
                    "tolerance": 0,
                },
            },
            {
                "id": "reason-rate",
                "prompt": (
                    "A car travels 150 km in 3 hours. What is its average speed "
                    "in km/h? Return only the numeric answer. Do not include units, "
                    "words, or explanation."
                ),
                "evaluator": {
                    "type": "numeric_answer",
                    "expected": 50,
                    "tolerance": 0,
                },
            },
            {
                "id": "reason-sequence",
                "prompt": (
                    "What is the next number: 2, 6, 12, 20, 30? Return only the "
                    "numeric answer. Do not include units, words, or explanation."
                ),
                "evaluator": {
                    "type": "numeric_answer",
                    "expected": 42,
                    "tolerance": 0,
                },
            },
        ],
    },
    {
        "name": "concurrency-health-check",
        "description": "Basic reliability and latency under increasing concurrency.",
        "concurrency_levels": [1, 5, 10],
        "cases": [
            {
                "id": "load-short",
                "prompt": "Reply with exactly: benchmark",
                "evaluator": {"type": "exact", "expected": "benchmark"},
            },
            {
                "id": "load-route",
                "prompt": "Reply with exactly: ready",
                "evaluator": {"type": "exact", "expected": "ready"},
            },
        ],
    },
]


PROFILE_ALIASES = {
    "chat-fast": "quick-migration-check",
    "classification": "exact-routing-check",
    "structured-extraction": "structured-output-check",
    "reasoning": "numeric-instruction-check",
    "load": "concurrency-health-check",
}


def normalize_profile_selector(selector: str) -> str:
    """Map legacy built-in profile names while preserving custom test names."""
    return ",".join(
        PROFILE_ALIASES.get(item.strip(), item.strip())
        for item in selector.split(",")
        if item.strip()
    )


def select_profiles(selector: str) -> list[dict[str, Any]]:
    requested = normalize_profile_selector(selector).split(",")
    names = [profile["name"] for profile in BUILTIN_PROFILES]
    if requested == ["all"]:
        return BUILTIN_PROFILES
    unknown = sorted(set(requested) - set(names))
    if unknown:
        raise ValueError(
            f"unknown profiles: {', '.join(unknown)}; choose all or {', '.join(names)}"
        )
    return [profile for profile in BUILTIN_PROFILES if profile["name"] in requested]


def evaluate_response(response: str, evaluator: dict[str, Any]) -> dict[str, Any]:
    evaluator_type = evaluator["type"]
    if evaluator_type == "nonempty":
        valid = bool(response.strip())
        return {
            "score": 1.0 if valid else 0.0,
            "valid": valid,
            "error": None if valid else "empty response",
        }
    if evaluator_type == "exact":
        valid = (
            response.strip().casefold() == str(evaluator["expected"]).strip().casefold()
        )
        return {
            "score": 1.0 if valid else 0.0,
            "valid": valid,
            "error": None if valid else "exact match failed",
        }
    if evaluator_type == "contains":
        expected = evaluator["contains"]
        valid = expected in response
        return {
            "score": 1.0 if valid else 0.0,
            "valid": valid,
            "error": None if valid else f"response did not contain {expected!r}",
        }
    if evaluator_type == "regex":
        pattern = evaluator["regex"]
        valid = re.search(pattern, response) is not None
        return {
            "score": 1.0 if valid else 0.0,
            "valid": valid,
            "error": None if valid else f"response did not match regex {pattern!r}",
        }
    if evaluator_type == "json_subset":
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            return {"score": 0.0, "valid": False, "error": "invalid JSON"}
        expected = evaluator["expected"]
        valid = isinstance(parsed, dict) and all(
            parsed.get(key) == value for key, value in expected.items()
        )
        return {
            "score": 1.0 if valid else 0.0,
            "valid": valid,
            "error": None if valid else "required JSON fields did not match",
        }
    if evaluator_type == "json_schema":
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            return {"score": 0.0, "valid": False, "error": "invalid JSON"}
        error = _validate_json_schema(parsed, evaluator["schema"])
        valid = error is None
        return {
            "score": 1.0 if valid else 0.0,
            "valid": valid,
            "error": error,
        }
    if evaluator_type == "numeric":
        try:
            actual = float(response.strip().replace(",", ""))
        except ValueError:
            return {"score": 0.0, "valid": False, "error": "not a numeric answer"}
        valid = abs(actual - float(evaluator["expected"])) <= float(
            evaluator.get("tolerance", 0)
        )
        return {
            "score": 1.0 if valid else 0.0,
            "valid": valid,
            "error": None if valid else "numeric answer outside tolerance",
        }
    if evaluator_type == "numeric_answer":
        matches = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", response)
        if not matches:
            return {"score": 0.0, "valid": False, "error": "not a numeric answer"}
        expected = float(evaluator["expected"])
        tolerance = float(evaluator.get("tolerance", 0))
        valid = any(
            abs(float(match.replace(",", "")) - expected) <= tolerance
            for match in matches
        )
        return {
            "score": 1.0 if valid else 0.0,
            "valid": valid,
            "error": None if valid else "numeric answer outside tolerance",
        }
    raise ValueError(f"unknown evaluator type {evaluator_type!r}")


def _validate_json_schema(
    value: Any, schema: dict[str, Any], path: str = ""
) -> str | None:
    expected_type = schema.get("type")
    label = path or "value"
    if expected_type == "object":
        if not isinstance(value, dict):
            return f"{label} must be an object"
        for key in schema.get("required", []):
            if key not in value:
                return f"{key} is required"
        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in value:
                child_path = f"{label}.{key}" if path else key
                error = _validate_json_schema(value[key], child_schema, child_path)
                if error:
                    return error
    elif expected_type == "array":
        if not isinstance(value, list):
            return f"{label} must be an array"
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < int(min_items):
            return f"{label} must contain at least {min_items} items"
        max_items = schema.get("maxItems")
        if max_items is not None and len(value) > int(max_items):
            return f"{label} must contain at most {max_items} items"
        if "items" in schema:
            for index, item in enumerate(value):
                error = _validate_json_schema(
                    item, schema["items"], f"{label}[{index}]"
                )
                if error:
                    return error
    elif expected_type == "string" and not isinstance(value, str):
        return f"{label} must be a string"
    elif expected_type == "number" and not isinstance(value, int | float):
        return f"{label} must be a number"
    elif expected_type == "integer" and not isinstance(value, int):
        return f"{label} must be an integer"
    elif expected_type == "boolean" and not isinstance(value, bool):
        return f"{label} must be a boolean"
    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(str(item) for item in schema["enum"])
        return f"{label} must be one of: {allowed}"
    return None
