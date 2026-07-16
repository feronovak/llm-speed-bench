from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from .security import require_http_url

TokenUsage = dict[str, int | None]
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


PROVIDER_DEFAULTS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "max_tokens_parameter": "max_completion_tokens",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key_env": "GEMINI_API_KEY",
    },
    "mock": {
        "base_url": "https://mock.local/v1",
    },
}


def _supports_temperature(model: dict[str, Any]) -> bool:
    explicit = model.get("supports_temperature")
    if explicit is not None:
        return bool(explicit)
    capabilities = model.get("capabilities", {})
    if isinstance(capabilities, dict) and "temperature" in capabilities:
        return bool(capabilities["temperature"])
    provider = model.get("provider")
    model_id = model["model"]
    if provider == "openai":
        return not (
            model_id == "gpt-5.5"
            or model_id.startswith("gpt-5.5-")
            or model_id.startswith("gpt-5.6-")
        )
    if provider == "anthropic":
        return model_id not in {
            "claude-sonnet-5",
            "claude-fable-5",
            "claude-opus-4-8",
        }
    return True


def _provider_options(options: dict[str, Any], provider: str) -> dict[str, Any]:
    configured = options.get("provider_options", {})
    if not isinstance(configured, dict):
        return {}
    provider_keys = set(PROVIDER_DEFAULTS) | {"openai_compatible", "all"}
    if any(key in provider_keys for key in configured):
        merged = dict(configured.get("all", {}))
        merged.update(configured.get(provider, {}))
        return merged
    return dict(configured)


def _retry_config(options: dict[str, Any]) -> dict[str, Any]:
    configured = options.get("retry", {})
    if configured is True:
        configured = {}
    if not isinstance(configured, dict):
        configured = {}
    return {
        "max_attempts": max(1, int(configured.get("max_attempts", 2))),
        "initial_delay_seconds": max(
            0.0, float(configured.get("initial_delay_seconds", 0.25))
        ),
        "max_delay_seconds": max(0.0, float(configured.get("max_delay_seconds", 4))),
        "backoff_multiplier": max(1.0, float(configured.get("backoff_multiplier", 2))),
        "jitter_seconds": max(0.0, float(configured.get("jitter_seconds", 0.1))),
        "retry_on": set(
            configured.get(
                "retry_on",
                ["rate_limit", "timeout", "transient_provider", "network"],
            )
        ),
    }


def _classify_failure(error: str, status_code: int | None = None) -> str:
    folded = error.casefold()
    if status_code == 404:
        return "not_found"
    if "environment variable" in folded or status_code in {401, 403}:
        return "credentials"
    if "unsupported" in folded and "parameter" in folded:
        return "unsupported_parameter"
    if status_code == 429 or "rate limit" in folded or "rate limited" in folded:
        return "rate_limit"
    if status_code == 408 or "timed out" in folded or "timeout" in folded:
        return "timeout"
    if status_code is not None and 500 <= status_code <= 599:
        return "transient_provider"
    return "provider_error"


def _classify_exception(exc: Exception) -> str:
    """Classify local transport errors without relying on OS message text."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, OSError):
        return "network"
    return _classify_failure(str(exc))


def _retry_delay(config: dict[str, Any], retry_index: int) -> float:
    delay = config["initial_delay_seconds"] * (
        config["backoff_multiplier"] ** max(0, retry_index - 1)
    )
    delay = min(delay, config["max_delay_seconds"])
    jitter = min(config["jitter_seconds"], config["max_delay_seconds"] - delay)
    return delay + random.uniform(0, jitter) if jitter else delay  # nosec B311


class ProviderClient(ABC):
    """Provider adapter contract.

    New providers only need to define endpoint/auth, request translation, and
    streamed event translation. The runner and metrics remain provider-neutral.
    """

    def __init__(self, model: dict[str, Any], timeout: float):
        self.model = model
        self.timeout = timeout

    @abstractmethod
    def endpoint(self) -> str:
        pass

    @abstractmethod
    def headers(self, api_key: str | None) -> dict[str, str]:
        pass

    @abstractmethod
    def body(self, prompt: str, options: dict[str, Any]) -> dict[str, Any]:
        pass

    @abstractmethod
    def parse_event(self, event: dict[str, Any]) -> tuple[str | None, TokenUsage]:
        """Return a text delta and any input/output token usage."""

    def run(self, prompt: str, request_options: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        key_env = self.model.get("api_key_env")
        api_key = os.environ.get(key_env) if key_env else None
        if key_env and not api_key:
            return self._failure(
                started,
                f"environment variable {key_env!r} is not set",
                attempts=1,
                retry_reasons=[],
                failure_category="credentials",
            )

        retry = _retry_config(request_options)
        retry_reasons: list[str] = []
        last_error = ""
        last_category = "provider_error"
        for attempt in range(1, retry["max_attempts"] + 1):
            attempt_started = time.perf_counter()
            first_token_at: float | None = None
            content: list[str] = []
            usage: dict[str, int] = {}
            try:
                endpoint = self.endpoint()
                require_http_url(endpoint)
                request = urllib.request.Request(
                    endpoint,
                    data=json.dumps(self.body(prompt, request_options)).encode(),
                    headers={
                        "Content-Type": "application/json",
                        **self.headers(api_key),
                        **self.model.get("headers", {}),
                    },
                    method="POST",
                )
                with urllib.request.urlopen(  # nosec B310
                    request, timeout=self.timeout
                ) as response:
                    response_bytes = 0
                    for raw_line in response:
                        response_bytes += len(raw_line)
                        if response_bytes > MAX_RESPONSE_BYTES:
                            raise ValueError(
                                "provider response exceeded the 8 MiB safety limit"
                            )
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload or payload == "[DONE]":
                            continue
                        text, event_usage = self.parse_event(json.loads(payload))
                        usage.update(
                            {
                                key: value
                                for key, value in event_usage.items()
                                if value is not None
                            }
                        )
                        if text:
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
                            content.append(text)
            except urllib.error.HTTPError as exc:
                detail = exc.read(2000).decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {detail}"
                last_category = _classify_failure(last_error, exc.code)
            except (OSError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                last_category = _classify_exception(exc)
            else:
                finished = time.perf_counter()
                output_tokens = usage.get("output_tokens")
                generation_seconds = (
                    finished - first_token_at if first_token_at else None
                )
                throughput = (
                    output_tokens / generation_seconds
                    if output_tokens is not None and generation_seconds
                    else None
                )
                return {
                    "ok": True,
                    "latency_seconds": finished - attempt_started,
                    "ttft_seconds": first_token_at - attempt_started
                    if first_token_at
                    else None,
                    "output_tokens_per_second": throughput,
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": output_tokens,
                    "response_chars": sum(map(len, content)),
                    "response": "".join(content),
                    "error": None,
                    "attempts": attempt,
                    "retry_count": attempt - 1,
                    "retry_reasons": retry_reasons,
                    "failure_category": None,
                }

            if (
                last_category not in retry["retry_on"]
                or attempt == retry["max_attempts"]
            ):
                return self._failure(
                    started,
                    last_error,
                    attempts=attempt,
                    retry_reasons=retry_reasons,
                    failure_category=last_category,
                )
            retry_reasons.append(last_category)
            delay = _retry_delay(retry, attempt)
            if delay:
                time.sleep(delay)

        return self._failure(
            started,
            last_error,
            attempts=retry["max_attempts"],
            retry_reasons=retry_reasons,
            failure_category=last_category,
        )

    @staticmethod
    def _failure(
        started: float,
        error: str,
        attempts: int = 1,
        retry_reasons: list[str] | None = None,
        failure_category: str | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "latency_seconds": time.perf_counter() - started,
            "ttft_seconds": None,
            "output_tokens_per_second": None,
            "input_tokens": None,
            "output_tokens": None,
            "response_chars": 0,
            "response": "",
            "error": error,
            "attempts": attempts,
            "retry_count": max(0, attempts - 1),
            "retry_reasons": retry_reasons or [],
            "failure_category": failure_category or _classify_failure(error),
        }


class OpenAICompatibleClient(ProviderClient):
    def endpoint(self) -> str:
        return self.model["base_url"].rstrip("/") + "/chat/completions"

    def headers(self, api_key: str | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def body(self, prompt: str, options: dict[str, Any]) -> dict[str, Any]:
        messages = []
        if options.get("system_prompt"):
            messages.append({"role": "system", "content": options["system_prompt"]})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": self.model["model"],
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if "temperature" in options and _supports_temperature(self.model):
            body["temperature"] = options["temperature"]
        limit = options.get("max_output_tokens", options.get("max_tokens"))
        if limit is not None:
            parameter = self.model.get("max_tokens_parameter", "max_tokens")
            body[parameter] = limit
        body.update(
            _provider_options(options, self.model.get("provider", "openai_compatible"))
        )
        return body

    def parse_event(self, event: dict[str, Any]) -> tuple[str | None, TokenUsage]:
        usage = event.get("usage") or {}
        normalized = {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
        }
        choices = event.get("choices") or []
        text = (choices[0].get("delta") or {}).get("content") if choices else None
        return text, normalized


class OpenAIResponsesClient(OpenAICompatibleClient):
    """OpenAI's Responses endpoint, used first for capability probes."""

    def endpoint(self) -> str:
        return self.model["base_url"].rstrip("/") + "/responses"

    def body(self, prompt: str, options: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model["model"],
            "input": prompt,
            "store": False,
        }
        limit = options.get("max_output_tokens", options.get("max_tokens"))
        if limit is not None:
            body["max_output_tokens"] = limit
        return body

    def run(self, prompt: str, request_options: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        key_env = self.model.get("api_key_env")
        api_key = os.environ.get(key_env) if key_env else None
        if key_env and not api_key:
            return self._failure(
                started, f"environment variable {key_env!r} is not set"
            )
        retry = _retry_config(request_options)
        retry_reasons: list[str] = []
        last_error = ""
        last_category = "provider_error"
        for attempt in range(1, retry["max_attempts"] + 1):
            attempt_started = time.perf_counter()
            try:
                endpoint = self.endpoint()
                require_http_url(endpoint)
                request = urllib.request.Request(
                    endpoint,
                    data=json.dumps(self.body(prompt, request_options)).encode(),
                    headers={
                        "Content-Type": "application/json",
                        **self.headers(api_key),
                        **self.model.get("headers", {}),
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # nosec B310
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError(
                        "provider response exceeded the 8 MiB safety limit"
                    )
                payload = json.loads(body)
            except urllib.error.HTTPError as exc:
                detail = exc.read(2000).decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {detail}"
                last_category = _classify_failure(last_error, exc.code)
            except (OSError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                last_category = _classify_exception(exc)
            else:
                usage = payload.get("usage") or {}
                text = str(payload.get("output_text") or "")
                if not text:
                    for output in payload.get("output") or []:
                        for content in output.get("content") or []:
                            if content.get("type") in {"output_text", "text"}:
                                text += str(content.get("text") or "")
                finished = time.perf_counter()
                return {
                    "ok": bool(text),
                    "latency_seconds": finished - attempt_started,
                    "ttft_seconds": None,
                    "output_tokens_per_second": None,
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "response_chars": len(text),
                    "response": text,
                    "error": None if text else "Responses API returned no text output",
                    "attempts": attempt,
                    "retry_count": attempt - 1,
                    "retry_reasons": retry_reasons,
                    "failure_category": None if text else "provider_error",
                }
            if (
                last_category not in retry["retry_on"]
                or attempt == retry["max_attempts"]
            ):
                return self._failure(
                    started, last_error, attempt, retry_reasons, last_category
                )
            retry_reasons.append(last_category)
            delay = _retry_delay(retry, attempt)
            if delay:
                time.sleep(delay)
        return self._failure(
            started, last_error, retry["max_attempts"], retry_reasons, last_category
        )


class AnthropicClient(ProviderClient):
    def endpoint(self) -> str:
        return self.model["base_url"].rstrip("/") + "/messages"

    def headers(self, api_key: str | None) -> dict[str, str]:
        return {
            "x-api-key": api_key or "",
            "anthropic-version": self.model.get("api_version", "2023-06-01"),
        }

    def body(self, prompt: str, options: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model["model"],
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "max_tokens": options.get(
                "max_output_tokens", options.get("max_tokens", 256)
            ),
        }
        if "temperature" in options and _supports_temperature(self.model):
            body["temperature"] = options["temperature"]
        if options.get("system_prompt"):
            body["system"] = options["system_prompt"]
        body.update(_provider_options(options, "anthropic"))
        return body

    def parse_event(self, event: dict[str, Any]) -> tuple[str | None, TokenUsage]:
        usage = event.get("usage") or (event.get("message") or {}).get("usage") or {}
        normalized = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        }
        delta = event.get("delta") or {}
        return delta.get("text"), normalized


class GeminiClient(ProviderClient):
    def endpoint(self) -> str:
        model = self.model["model"]
        return (
            self.model["base_url"].rstrip("/")
            + f"/models/{model}:streamGenerateContent?alt=sse"
        )

    def headers(self, api_key: str | None) -> dict[str, str]:
        return {"x-goog-api-key": api_key or ""}

    def body(self, prompt: str, options: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {},
        }
        generation = body["generationConfig"]
        if "temperature" in options and _supports_temperature(self.model):
            generation["temperature"] = options["temperature"]
        limit = options.get("max_output_tokens", options.get("max_tokens"))
        if limit is not None:
            generation["maxOutputTokens"] = limit
        if options.get("system_prompt"):
            body["systemInstruction"] = {"parts": [{"text": options["system_prompt"]}]}
        provider_options = _provider_options(options, "gemini")
        provider_generation = provider_options.pop("generationConfig", {})
        generation.update(provider_generation)
        body.update(provider_options)
        return body

    def parse_event(self, event: dict[str, Any]) -> tuple[str | None, TokenUsage]:
        usage = event.get("usageMetadata") or {}
        normalized = {
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
        }
        candidates = event.get("candidates") or []
        parts = (
            ((candidates[0].get("content") or {}).get("parts") or [])
            if candidates
            else []
        )
        text = (
            "".join(part.get("text", "") for part in parts if not part.get("thought"))
            or None
        )
        return text, normalized


class MockClient(ProviderClient):
    def endpoint(self) -> str:
        return self.model["base_url"]

    def headers(self, api_key: str | None) -> dict[str, str]:
        return {}

    def body(self, prompt: str, options: dict[str, Any]) -> dict[str, Any]:
        return {"prompt": prompt, **options}

    def parse_event(self, event: dict[str, Any]) -> tuple[str | None, TokenUsage]:
        return event.get("text"), event.get("usage", {})

    def run(self, prompt: str, request_options: dict[str, Any]) -> dict[str, Any]:
        response = str(
            self.model.get("response", request_options.get("response", "ok"))
        )
        input_tokens = max(1, len(prompt.split()))
        output_tokens = max(1, len(response.split()))
        return {
            "ok": True,
            "latency_seconds": float(self.model.get("latency_seconds", 0.001)),
            "ttft_seconds": float(self.model.get("ttft_seconds", 0.001)),
            "output_tokens_per_second": output_tokens
            / float(self.model.get("latency_seconds", 0.001)),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "response_chars": len(response),
            "response": response,
            "error": None,
        }


def create_client(model: dict[str, Any], timeout: float) -> ProviderClient:
    provider = model.get("provider", "openai_compatible")
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    resolved = {**defaults, **model}
    if "base_url" not in resolved:
        raise ValueError(
            f"model {model.get('name', model.get('model'))!r} requires base_url "
            f"for provider {provider!r}"
        )
    require_http_url(resolved["base_url"], resolve_host=False)
    adapters: dict[str, Any] = {
        "openai": OpenAICompatibleClient,
        "openrouter": OpenAICompatibleClient,
        "xai": OpenAICompatibleClient,
        "openai_compatible": OpenAICompatibleClient,
        "anthropic": AnthropicClient,
        "gemini": GeminiClient,
        "mock": MockClient,
    }
    try:
        adapter = (
            OpenAIResponsesClient
            if resolved.get("adapter") == "openai_responses"
            else adapters[provider]
        )
    except KeyError as exc:
        raise ValueError(
            f"unsupported provider {provider!r}; choose {', '.join(sorted(adapters))}"
        ) from exc
    return adapter(resolved, timeout)
