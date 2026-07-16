import io
import urllib.error

import pytest

from llm_bench.client import (
    AnthropicClient,
    GeminiClient,
    MAX_RESPONSE_BYTES,
    OpenAICompatibleClient,
    OpenAIResponsesClient,
    _retry_delay,
    create_client,
)


def test_openai_responses_adapter_uses_minimal_safe_probe_request():
    client = create_client(
        {"provider": "openai", "model": "gpt-test", "adapter": "openai_responses"},
        10,
    )

    assert isinstance(client, OpenAIResponsesClient)
    assert client.endpoint() == "https://api.openai.com/v1/responses"
    assert client.body("Reply with OK.", {"max_output_tokens": 32}) == {
        "model": "gpt-test",
        "input": "Reply with OK.",
        "max_output_tokens": 32,
        "store": False,
    }


@pytest.fixture(autouse=True)
def _resolve_provider_hosts_to_a_public_address(monkeypatch):
    """Keep unit fixtures independent from the machine's DNS configuration."""
    monkeypatch.setattr(
        "llm_bench.security.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))],
    )


def test_factory_applies_openai_defaults():
    client = create_client({"provider": "openai", "model": "model-a"}, 10)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.model["base_url"] == "https://api.openai.com/v1"
    assert client.model["api_key_env"] == "OPENAI_API_KEY"
    body = client.body("hello", {"max_output_tokens": 1})
    assert body["max_completion_tokens"] == 1
    assert "max_tokens" not in body


def test_runtime_url_validation_failure_becomes_a_normal_api_failure(monkeypatch):
    client = OpenAICompatibleClient(
        {
            "provider": "openai_compatible",
            "model": "broken",
            "base_url": "https://api.example.test/v1",
        },
        10,
    )
    monkeypatch.setattr(
        "llm_bench.client.require_http_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("URL host could not be resolved: 'api.example.test'")
        ),
    )

    result = client.run("Reply with ok.", {"retry": {"max_attempts": 1}})

    assert result["ok"] is False
    assert result["failure_category"] == "provider_error"


@pytest.mark.parametrize(
    "model", ["gpt-5.5", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
)
def test_current_gpt_models_omit_unsupported_temperature(model):
    client = create_client({"provider": "openai", "model": model}, 10)
    body = client.body("hello", {"temperature": 0, "max_output_tokens": 16})
    assert "temperature" not in body

    older = create_client({"provider": "openai", "model": "gpt-5.4-mini"}, 10)
    assert older.body("hello", {"temperature": 0})["temperature"] == 0


def test_model_can_explicitly_override_temperature_support():
    unsupported = create_client(
        {
            "provider": "openai",
            "model": "custom-no-temp",
            "capabilities": {"temperature": False},
        },
        10,
    )
    assert "temperature" not in unsupported.body("hello", {"temperature": 0.5})

    supported = create_client(
        {"provider": "openai", "model": "gpt-5.5", "supports_temperature": True},
        10,
    )
    assert supported.body("hello", {"temperature": 0.5})["temperature"] == 0.5


def test_openrouter_uses_compatible_adapter():
    client = create_client({"provider": "openrouter", "model": "vendor/model"}, 10)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.model["base_url"] == "https://openrouter.ai/api/v1"
    body = client.body("hello", {"max_output_tokens": 1})
    assert body["max_tokens"] == 1
    assert "max_completion_tokens" not in body


def test_openrouter_applies_provider_specific_options():
    client = create_client({"provider": "openrouter", "model": "vendor/model"}, 10)
    body = client.body(
        "hello",
        {
            "provider_options": {
                "gemini": {
                    "generationConfig": {"responseMimeType": "application/json"}
                },
                "openrouter": {
                    "response_format": {"type": "json_object"},
                    "include_reasoning": False,
                    "reasoning": {"enabled": False},
                },
            }
        },
    )

    assert body["response_format"] == {"type": "json_object"}
    assert body["include_reasoning"] is False
    assert body["reasoning"] == {"enabled": False}
    assert "gemini" not in body


def test_compatible_client_ignores_other_provider_options():
    client = create_client({"provider": "openai", "model": "model-a"}, 10)
    body = client.body(
        "hello",
        {
            "provider_options": {
                "gemini": {"generationConfig": {"responseMimeType": "application/json"}}
            }
        },
    )
    assert "generationConfig" not in body
    assert "gemini" not in body


def test_xai_uses_native_compatible_api_defaults():
    client = create_client({"provider": "xai", "model": "grok-4.3"}, 10)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.model["base_url"] == "https://api.x.ai/v1"
    assert client.model["api_key_env"] == "XAI_API_KEY"
    assert client.headers("secret") == {"Authorization": "Bearer secret"}
    body = client.body("hello", {"max_output_tokens": 16})
    assert body["max_tokens"] == 16


def test_anthropic_request_and_events():
    client = create_client({"provider": "anthropic", "model": "claude-test"}, 10)
    assert isinstance(client, AnthropicClient)
    body = client.body(
        "hello",
        {"system_prompt": "brief", "temperature": 0, "max_output_tokens": 42},
    )
    assert body["system"] == "brief"
    assert body["max_tokens"] == 42
    text, usage = client.parse_event(
        {"delta": {"text": "hi"}, "usage": {"output_tokens": 2}}
    )
    assert text == "hi"
    assert usage["output_tokens"] == 2


def test_current_anthropic_models_omit_unsupported_temperature():
    for model in ("claude-sonnet-5", "claude-fable-5", "claude-opus-4-8"):
        client = create_client({"provider": "anthropic", "model": model}, 10)
        assert "temperature" not in client.body(
            "hello", {"temperature": 0, "max_output_tokens": 16}
        )


def test_gemini_request_and_events():
    client = create_client({"provider": "gemini", "model": "gemini-test"}, 10)
    assert isinstance(client, GeminiClient)
    body = client.body("hello", {"max_output_tokens": 42})
    assert body["generationConfig"]["maxOutputTokens"] == 42
    text, usage = client.parse_event(
        {
            "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 2,
            },
        }
    )
    assert text == "hi"
    assert usage == {"input_tokens": 1, "output_tokens": 2}


def test_gemini_merges_provider_specific_generation_config():
    client = create_client({"provider": "gemini", "model": "gemini-test"}, 10)
    body = client.body(
        "hello",
        {
            "temperature": 0,
            "max_output_tokens": 42,
            "provider_options": {
                "gemini": {
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "thinkingConfig": {
                            "includeThoughts": False,
                        },
                    }
                }
            },
        },
    )

    assert body["generationConfig"] == {
        "temperature": 0,
        "maxOutputTokens": 42,
        "responseMimeType": "application/json",
        "thinkingConfig": {"includeThoughts": False},
    }


def test_gemini_parser_ignores_thought_parts():
    client = create_client({"provider": "gemini", "model": "gemini-test"}, 10)

    text, usage = client.parse_event(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Draft: I should make JSON.", "thought": True},
                            {"text": '{"questions":[]}'},
                        ]
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 8,
            },
        }
    )

    assert text == '{"questions":[]}'
    assert usage == {"input_tokens": 1, "output_tokens": 8}


def test_custom_compatible_provider():
    client = create_client(
        {
            "provider": "openai_compatible",
            "model": "local",
            "base_url": "http://localhost:1234/v1",
        },
        10,
    )
    assert client.endpoint() == "http://localhost:1234/v1/chat/completions"


@pytest.mark.parametrize(
    ("base_url", "message"),
    [
        ("file:///etc", "http or https"),
        ("https:///missing-host", "http or https"),
        ("https://user:secret@example.test/v1", "embedded credentials"),
    ],
)
def test_custom_provider_rejects_unsafe_base_url(base_url, message):
    with pytest.raises(ValueError, match=message):
        create_client(
            {
                "provider": "openai_compatible",
                "model": "local",
                "base_url": base_url,
            },
            10,
        )


def test_compatible_client_streams_text_and_usage(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            return iter(
                [
                    b'data: {"choices":[{"delta":{"content":"hi"}}]}\n',
                    b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":1}}\n',
                    b"data: [DONE]\n",
                ]
            )

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout: FakeResponse()
    )
    sample = create_client({"provider": "openai", "model": "model-a"}, 10).run(
        "hello", {"max_output_tokens": 4}
    )
    assert sample["ok"] is True
    assert sample["response"] == "hi"
    assert sample["input_tokens"] == 3
    assert sample["output_tokens"] == 1
    assert sample["error"] is None


def test_client_reports_missing_key_and_http_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = create_client({"provider": "openai", "model": "model-a"}, 10)
    assert "OPENAI_API_KEY" in client.run("hello", {})["error"]

    monkeypatch.setenv("OPENAI_API_KEY", "test")

    def raise_http_error(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "rate limited",
            {},
            io.BytesIO(b'{"error":"rate limited"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    sample = client.run("hello", {})
    assert sample["ok"] is False
    assert sample["error"] == 'HTTP 429: {"error":"rate limited"}'


def test_client_retries_retryable_http_error_and_records_attempts(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            return iter(
                [
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
                    b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1}}\n',
                    b"data: [DONE]\n",
                ]
            )

    calls = 0

    def flaky_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limited",
                {},
                io.BytesIO(b'{"error":"rate limited"}'),
            )
        return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr("urllib.request.urlopen", flaky_urlopen)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    sample = create_client({"provider": "openai", "model": "model-a"}, 10).run(
        "hello",
        {
            "retry": {
                "max_attempts": 2,
                "initial_delay_seconds": 0,
                "jitter": False,
            }
        },
    )

    assert sample["ok"] is True
    assert sample["response"] == "ok"
    assert sample["attempts"] == 2
    assert sample["retry_count"] == 1
    assert sample["retry_reasons"] == ["rate_limit"]


def test_client_retries_rate_limit_once_by_default(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            return iter(
                [
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
                    b"data: [DONE]\n",
                ]
            )

    calls = 0

    def flaky_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limited",
                {},
                io.BytesIO(b'{"error":"rate limited"}'),
            )
        return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr("urllib.request.urlopen", flaky_urlopen)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    sample = create_client({"provider": "openai", "model": "model-a"}, 10).run(
        "hello", {}
    )

    assert calls == 2
    assert sample["ok"] is True
    assert sample["retry_reasons"] == ["rate_limit"]


def test_client_does_not_retry_non_retryable_http_error(monkeypatch):
    calls = 0

    def bad_request(request, timeout):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "bad request",
            {},
            io.BytesIO(b'{"error":"unsupported parameter"}'),
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr("urllib.request.urlopen", bad_request)

    sample = create_client({"provider": "openai", "model": "model-a"}, 10).run(
        "hello", {"retry": {"max_attempts": 3, "initial_delay_seconds": 0}}
    )

    assert calls == 1
    assert sample["ok"] is False
    assert sample["attempts"] == 1
    assert sample["retry_count"] == 0
    assert sample["failure_category"] == "unsupported_parameter"


def test_client_rejects_an_oversized_streaming_response(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            return iter([b"data: " + b"x" * MAX_RESPONSE_BYTES])

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout: FakeResponse()
    )

    sample = create_client({"provider": "openai", "model": "model-a"}, 10).run(
        "hello", {"retry": {"max_attempts": 1}}
    )

    assert sample["ok"] is False
    assert "8 MiB safety limit" in sample["error"]


def test_retry_delay_adds_bounded_jitter(monkeypatch):
    monkeypatch.setattr("llm_bench.client.random.uniform", lambda low, high: 0.125)

    delay = _retry_delay(
        {
            "initial_delay_seconds": 0.5,
            "max_delay_seconds": 1,
            "backoff_multiplier": 2,
            "jitter_seconds": 0.2,
        },
        1,
    )

    assert delay == 0.625


def test_openai_compatible_provider_options_are_not_sent_as_a_bogus_field():
    client = create_client(
        {
            "provider": "openai_compatible",
            "model": "local",
            "base_url": "https://example.test/v1",
        },
        10,
    )

    body = client.body(
        "hello", {"provider_options": {"openai_compatible": {"seed": 7}}}
    )

    assert body["seed"] == 7
    assert "openai_compatible" not in body


def test_gemini_honors_explicit_temperature_capability_override():
    client = create_client(
        {"provider": "gemini", "model": "gemini-test", "supports_temperature": False},
        10,
    )

    assert (
        "temperature"
        not in client.body("hello", {"temperature": 0.2})["generationConfig"]
    )


def test_statusless_malformed_stream_is_not_retried(monkeypatch):
    calls = 0

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            return iter([b"data: not-json\n"])

    def bad_stream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr("urllib.request.urlopen", bad_stream)
    sample = create_client({"provider": "openai", "model": "model-a"}, 10).run(
        "hello", {"retry": {"max_attempts": 3, "initial_delay_seconds": 0}}
    )

    assert calls == 1
    assert sample["failure_category"] == "provider_error"


def test_openai_responses_honors_retry_configuration(monkeypatch):
    calls = 0

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, *_args):
            return b'{"output_text":"ok","usage":{}}'

    def flaky(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "rate", {}, io.BytesIO(b"rate limited")
            )
        return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr("urllib.request.urlopen", flaky)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    sample = create_client(
        {"provider": "openai", "model": "model-a", "adapter": "openai_responses"}, 10
    ).run("hello", {"retry": {"max_attempts": 2, "initial_delay_seconds": 0}})

    assert calls == 2
    assert sample["retry_reasons"] == ["rate_limit"]


def test_success_latency_excludes_previous_retry_and_backoff(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            return iter(
                [
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
                    b"data: [DONE]\n",
                ]
            )

    calls = 0

    def flaky(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "rate", {}, io.BytesIO()
            )
        return FakeResponse()

    timestamps = iter([0.0, 1.0, 10.0, 12.0, 15.0])
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr("urllib.request.urlopen", flaky)
    monkeypatch.setattr("llm_bench.client.time.perf_counter", lambda: next(timestamps))
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    sample = create_client({"provider": "openai", "model": "model-a"}, 10).run(
        "hello", {"retry": {"max_attempts": 2, "initial_delay_seconds": 1}}
    )

    assert sample["latency_seconds"] == 5.0
    assert sample["ttft_seconds"] == 2.0


def test_transient_socket_errors_are_retried_by_exception_type(monkeypatch):
    calls = 0

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            return iter([b'data: {"choices":[{"delta":{"content":"ok"}}]}\n'])

    def flaky(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionRefusedError("Connection refused")
        return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr("urllib.request.urlopen", flaky)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    sample = create_client({"provider": "openai", "model": "model-a"}, 10).run(
        "hello", {"retry": {"max_attempts": 2, "initial_delay_seconds": 0}}
    )

    assert calls == 2
    assert sample["retry_reasons"] == ["network"]
