from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_KEY_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "x-api-key",
}

_SECRET_TEXT_PATTERNS: list[tuple[re.Pattern[str], int | None]] = [
    (re.compile(r"(?i)(authorization\s*:?\s*bearer\s+)([A-Za-z0-9._:/+=-]+)"), 1),
    (re.compile(r"(?i)(x-api-key\s*:?\s*)([A-Za-z0-9._:/+=-]+)"), 1),
    (re.compile(r"\bsk-[A-Za-z0-9._-]+\b"), None),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"), None),
    (re.compile(r"\bxai-[A-Za-z0-9_-]{16,}\b"), None),
]


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    if normalized.endswith("_env"):
        return False
    parts = set(normalized.split("_"))
    return (
        key.casefold() in _SECRET_KEY_NAMES
        or normalized in _SECRET_KEY_NAMES
        or bool(parts & {"token", "secret", "password"})
        or normalized.endswith("_api_key")
    )


def _redact_text(value: str) -> str:
    redacted = value
    for pattern, prefix_group in _SECRET_TEXT_PATTERNS:
        if prefix_group is None:
            redacted = pattern.sub(REDACTED, redacted)
        else:
            redacted = pattern.sub(
                lambda match: match.group(prefix_group) + REDACTED, redacted
            )
    return redacted


def _redact_headers(value: Any) -> Any:
    if not isinstance(value, dict):
        return REDACTED
    return {
        _redact_text(str(key)) if isinstance(key, str) else key: REDACTED
        for key in value
    }


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            safe_key = _redact_text(str(key)) if isinstance(key, str) else key
            if isinstance(key, str) and key.casefold() == "headers":
                redacted[safe_key] = _redact_headers(item)
            elif isinstance(key, str) and _is_sensitive_key(key):
                redacted[safe_key] = REDACTED
            else:
                redacted[safe_key] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, str):
        return _redact_text(value)
    return value
