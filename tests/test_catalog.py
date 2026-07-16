import pytest

from llm_bench.catalog import classify_catalog_model, discover_models, resolve_models


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            {
                "model": "vendor/chat",
                "capabilities": {
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
            },
            ("text-chat", "official"),
        ),
        (
            {
                "model": "vendor/image",
                "capabilities": {"output_modalities": ["image"]},
            },
            ("image", "official"),
        ),
        ({"model": "gpt-realtime-2"}, ("realtime", "heuristic")),
        ({"model": "future-model"}, ("unknown", "unknown")),
    ],
)
def test_catalog_classification_prefers_metadata_and_is_conservative(model, expected):
    classified = classify_catalog_model(model)

    assert (classified["catalog_type"], classified["catalog_confidence"]) == expected


def test_catalog_ready_text_metadata_beats_a_misleading_model_name():
    classified = classify_catalog_model(
        {"model": "claude-omni-text", "capabilities": {"text_generation": "ready"}}
    )

    assert classified["catalog_type"] == "text-ready"


def test_anthropic_catalog_preserves_official_capabilities(monkeypatch):
    monkeypatch.setattr(
        "llm_bench.catalog._get_json",
        lambda *args, **kwargs: {
            "data": [
                {
                    "id": "claude-test",
                    "display_name": "Claude Test",
                    "capabilities": {"structured_outputs": {"supported": True}},
                }
            ]
        },
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    model = discover_models({"provider": "anthropic", "limit": 1})[0]

    assert model["catalog_type"] == "text-ready"
    assert model["capabilities"]["structured_outputs"]["supported"] is True
    assert model["capability_evidence"][0]["source"] == "official"


def test_xai_uses_language_models_for_official_modalities(monkeypatch):
    captured = []

    def fake_get_json(url, *args, **kwargs):
        captured.append(url)
        if not url.endswith("/language-models"):
            return {"models": []}
        return {
            "models": [
                {
                    "id": "grok-text",
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                    "fingerprint": "fp-test",
                }
            ]
        }

    monkeypatch.setattr("llm_bench.catalog._get_json", fake_get_json)
    monkeypatch.setenv("XAI_API_KEY", "test")

    model = discover_models({"provider": "xai", "limit": 1})[0]

    assert captured == [
        "https://api.x.ai/v1/language-models",
        "https://api.x.ai/v1/image-generation-models",
        "https://api.x.ai/v1/video-generation-models",
    ]
    assert model["catalog_type"] == "text-ready"
    assert model["capabilities"]["fingerprint"] == "fp-test"


def test_openai_text_family_is_visible_as_a_probe_candidate(monkeypatch):
    monkeypatch.setattr(
        "llm_bench.catalog._get_json",
        lambda *args, **kwargs: {"data": [{"id": "gpt-5.5"}]},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    model = discover_models({"provider": "openai", "limit": 1})[0]

    assert model["catalog_type"] == "text-candidate"
    assert model["capabilities"]["adapter"] == "openai_responses"


def test_gemini_tts_is_not_a_generic_text_probe(monkeypatch):
    monkeypatch.setattr(
        "llm_bench.catalog._get_json",
        lambda *args, **kwargs: {
            "models": [
                {
                    "name": "models/gemini-2.5-flash-preview-tts",
                    "supportedGenerationMethods": ["generateContent"],
                }
            ]
        },
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test")

    model = discover_models({"provider": "gemini", "limit": 1})[0]

    assert model["catalog_type"] == "audio"


def test_openrouter_normalization_and_limit(monkeypatch):
    monkeypatch.setattr(
        "llm_bench.catalog._get_json",
        lambda *args, **kwargs: {
            "data": [
                {
                    "id": "vendor/reasoner",
                    "name": "Reasoner",
                    "created": 2,
                    "context_length": 1000,
                    "supported_parameters": ["reasoning", "tools"],
                    "architecture": {
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                    },
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                },
                {
                    "id": "vendor/other",
                    "name": "Other",
                    "created": 1,
                    "supported_parameters": [],
                    "pricing": {},
                },
            ]
        },
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    models = discover_models(
        {"provider": "openrouter", "include": "reason", "limit": 1}
    )
    assert [model["model"] for model in models] == ["vendor/reasoner"]
    assert models[0]["capabilities"]["reasoning"] is True
    assert models[0]["input_cost_per_million"] == 1
    assert models[0]["output_cost_per_million"] == 2
    assert models[0]["pricing_metadata"]["source"] == "openrouter routed"


def test_gemini_filters_non_generation_models(monkeypatch):
    monkeypatch.setattr(
        "llm_bench.catalog._get_json",
        lambda *args, **kwargs: {
            "models": [
                {
                    "name": "models/gemini-test",
                    "displayName": "Gemini Test",
                    "supportedGenerationMethods": ["generateContent"],
                    "thinking": True,
                },
                {
                    "name": "models/embed-test",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        },
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    models = discover_models({"provider": "gemini", "limit": 5})
    assert [model["model"] for model in models] == ["gemini-test"]
    assert models[0]["capabilities"]["reasoning"] is True


def test_xai_catalog_uses_native_language_models_endpoint_and_pricing(monkeypatch):
    captured = []

    def fake_get_json(url, key_env, headers=None):
        captured.append((url, key_env))
        if not url.endswith("/language-models"):
            return {"models": []}
        return {
            "models": [
                {
                    "id": "grok-4.3",
                    "created": 1,
                    "owned_by": "xai",
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                }
            ]
        }

    monkeypatch.setattr("llm_bench.catalog._get_json", fake_get_json)
    monkeypatch.setenv("XAI_API_KEY", "test")
    models = discover_models({"provider": "xai", "limit": 1})
    assert captured == [
        ("https://api.x.ai/v1/language-models", "XAI_API_KEY"),
        ("https://api.x.ai/v1/image-generation-models", "XAI_API_KEY"),
        ("https://api.x.ai/v1/video-generation-models", "XAI_API_KEY"),
    ]
    assert models[0]["provider"] == "xai"
    assert models[0]["model"] == "grok-4.3"
    assert models[0]["input_cost_per_million"] == 1.25
    assert models[0]["output_cost_per_million"] == 2.5


def test_resolve_deduplicates_explicit_and_discovered(monkeypatch):
    monkeypatch.setattr(
        "llm_bench.catalog.discover_models",
        lambda source: [{"provider": "openai", "model": "same"}],
    )
    models = resolve_models(
        {
            "models": [{"provider": "openai", "model": "same"}],
            "discovery": [{"provider": "openai", "limit": 1}],
        }
    )
    assert len(models) == 1


def test_resolve_enriches_matching_direct_model_from_openrouter_metadata():
    models = resolve_models(
        {
            "models": [
                {"provider": "openai", "model": "gpt-new"},
                {
                    "provider": "openrouter",
                    "model": "openai/gpt-new",
                    "capabilities": {
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                        "supported_parameters": ["max_tokens"],
                    },
                },
            ]
        }
    )

    assert models[0]["catalog_type"] == "text-chat"
    assert models[0]["catalog_confidence"] == "aggregator"
    assert models[0]["capabilities"]["supported_parameters"] == ["max_tokens"]


def test_resolve_enriches_anthropic_version_separator_match():
    models = resolve_models(
        {
            "models": [
                {
                    "provider": "anthropic",
                    "model": "claude-opus-4-8",
                    "capabilities": {"text_generation": "ready"},
                },
                {
                    "provider": "openrouter",
                    "model": "anthropic/claude-opus-4.8",
                    "capabilities": {
                        "output_modalities": ["text"],
                        "supported_parameters": ["temperature"],
                    },
                },
            ]
        }
    )

    assert models[0]["capabilities"]["supported_parameters"] == ["temperature"]
    assert models[0]["capability_evidence"][-1]["source"] == "openrouter-normalized"


def test_resolve_adds_public_registry_pricing_and_preserves_overrides():
    models = resolve_models(
        {
            "models": [
                {"provider": "openai", "model": "gpt-5.4-mini"},
                {"provider": "gemini", "model": "gemini-3.5-flash"},
                {"provider": "anthropic", "model": "claude-opus-4-8"},
                {
                    "provider": "openai",
                    "model": "gpt-4.1",
                    "input_cost_per_million": 99,
                    "output_cost_per_million": 100,
                },
            ]
        }
    )
    assert models[0]["input_cost_per_million"] == 0.75
    assert models[0]["output_cost_per_million"] == 4.5
    assert models[1]["input_cost_per_million"] == 1.5
    assert models[1]["output_cost_per_million"] == 9
    assert models[2]["input_cost_per_million"] == 5
    assert models[2]["output_cost_per_million"] == 25
    assert models[3]["input_cost_per_million"] == 99
    assert models[3]["output_cost_per_million"] == 100


def test_catalog_rejects_non_http_base_url_before_opening(monkeypatch):
    opened = False

    def should_not_open(*args, **kwargs):
        nonlocal opened
        opened = True

    monkeypatch.setattr("urllib.request.urlopen", should_not_open)
    with pytest.raises(ValueError, match="http or https"):
        discover_models(
            {
                "provider": "openai_compatible",
                "base_url": "file:///etc",
                "limit": 1,
            }
        )
    assert opened is False


def test_openrouter_encodes_list_query_values_as_repeated_parameters(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "llm_bench.catalog._get_json",
        lambda url, *_args, **_kwargs: captured.append(url) or {"data": []},
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    discover_models(
        {
            "provider": "openrouter",
            "output_modalities": ["text", "image"],
            "limit": 1,
        }
    )

    assert "output_modalities=text&output_modalities=image" in captured[0]
