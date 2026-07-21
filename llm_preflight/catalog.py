from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any

from .client import PROVIDER_DEFAULTS
from .pricing import apply_public_pricing
from .security import open_public_url, require_http_url


_MAX_CATALOG_RESPONSE_BYTES = 8 * 1024 * 1024

_NON_CHAT_NAME_TYPES = {
    "realtime": "realtime",
    "transcribe": "audio",
    "tts": "audio",
    "audio": "audio",
    "image": "image",
    "video": "video",
    "embed": "embedding",
    "computer-use": "agent",
    "robotics": "agent",
    "multi-agent": "agent",
    "omni": "media",
}


def classify_catalog_model(model: dict[str, Any]) -> dict[str, Any]:
    """Attach a conservative benchmark category without guessing chat support."""
    classified = dict(model)
    if classified.get("catalog_type"):
        classified.setdefault("catalog_confidence", "manual")
        return classified
    capabilities = classified.get("capabilities") or {}
    name = str(classified.get("model", "")).casefold()
    output = {
        str(item).casefold() for item in capabilities.get("output_modalities") or []
    }
    if capabilities.get("text_generation") == "ready":
        classified["catalog_type"] = "text-ready"
        classified["catalog_confidence"] = "official"
        return classified
    named_type = next(
        (kind for term, kind in _NON_CHAT_NAME_TYPES.items() if term in name), None
    )
    if named_type and not output:
        classified["catalog_type"] = named_type
        classified["catalog_confidence"] = "heuristic"
        return classified
    if capabilities.get("text_generation") == "candidate":
        classified["catalog_type"] = "text-candidate"
        classified["catalog_confidence"] = "official"
        return classified
    if output:
        if "text" in output:
            catalog_type = "text-chat"
        elif "image" in output:
            catalog_type = "image"
        elif "audio" in output:
            catalog_type = "audio"
        elif "video" in output:
            catalog_type = "video"
        elif "embeddings" in output or "embedding" in output:
            catalog_type = "embedding"
        else:
            catalog_type = "unknown"
        confidence = "official"
    else:
        catalog_type = next(
            (kind for term, kind in _NON_CHAT_NAME_TYPES.items() if term in name),
            "unknown",
        )
        confidence = "heuristic" if catalog_type != "unknown" else "unknown"
    classified["catalog_type"] = catalog_type
    classified["catalog_confidence"] = confidence
    return classified


def _get_json(
    url: str, key_env: str | None, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    require_http_url(url)
    key = os.environ.get(key_env) if key_env else None
    if key_env and not key:
        raise ValueError(f"environment variable {key_env!r} is not set")
    request_headers = dict(headers or {})
    if key:
        request_headers.setdefault("Authorization", f"Bearer {key}")
    request = urllib.request.Request(url, headers=request_headers)
    with open_public_url(request, timeout=30) as response:
        body = response.read(_MAX_CATALOG_RESPONSE_BYTES + 1)
    if len(body) > _MAX_CATALOG_RESPONSE_BYTES:
        raise ValueError("catalog response exceeded the 8 MiB safety limit")
    return json.loads(body)


def _base(source: dict[str, Any]) -> dict[str, Any]:
    provider = source["provider"]
    return {**PROVIDER_DEFAULTS.get(provider, {}), **source}


def _openai(source: dict[str, Any]) -> list[dict[str, Any]]:
    config = _base(source)
    payload = _get_json(
        config["base_url"].rstrip("/") + "/models",
        config.get("api_key_env"),
        config.get("headers"),
    )
    return [
        {
            "name": item["id"],
            "provider": source["provider"],
            "model": item["id"],
            "created": item.get("created"),
            "owned_by": item.get("owned_by"),
            "capabilities": {
                "reasoning": None,
                **_openai_capabilities(item["id"]),
            },
            "capability_evidence": [{"source": "official-id", "confidence": "low"}],
            "catalog_metadata": item,
        }
        for item in payload.get("data", [])
    ]


def _openai_capabilities(model_id: str) -> dict[str, Any]:
    name = model_id.casefold()
    if name.startswith("gpt-") and not any(
        term in name for term in ("realtime", "audio", "image", "transcribe", "tts")
    ):
        return {
            "text_generation": "candidate",
            "adapter": "openai_responses",
        }
    return {}


def _anthropic(source: dict[str, Any]) -> list[dict[str, Any]]:
    config = _base(source)
    headers = {
        "x-api-key": os.environ.get(config.get("api_key_env", ""), ""),
        "anthropic-version": config.get("api_version", "2023-06-01"),
        **config.get("headers", {}),
    }
    payload = _get_json(
        config["base_url"].rstrip("/") + "/models?limit=1000",
        config.get("api_key_env"),
        headers,
    )
    return [
        {
            "name": item.get("display_name", item["id"]),
            "provider": "anthropic",
            "model": item["id"],
            "created": item.get("created_at"),
            "capabilities": {
                **(item.get("capabilities") or {}),
                "reasoning": (item.get("capabilities") or {}).get("thinking"),
                "text_generation": "ready",
                "adapter": "anthropic_messages",
            },
            "capability_evidence": [{"source": "official", "confidence": "high"}],
            "catalog_metadata": item,
        }
        for item in payload.get("data", [])
    ]


def _gemini(source: dict[str, Any]) -> list[dict[str, Any]]:
    config = _base(source)
    key = os.environ.get(config.get("api_key_env", ""))
    if not key:
        raise ValueError(
            f"environment variable {config.get('api_key_env')!r} is not set"
        )
    url = config["base_url"].rstrip("/") + "/models?pageSize=1000"
    payload = _get_json(url, None, {"x-goog-api-key": key, **config.get("headers", {})})
    result = []
    for item in payload.get("models", []):
        methods = item.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        model_id = item["name"].removeprefix("models/")
        result.append(
            {
                "name": item.get("displayName", model_id),
                "provider": "gemini",
                "model": model_id,
                "context_length": item.get("inputTokenLimit"),
                "max_output_tokens": item.get("outputTokenLimit"),
                "capabilities": {
                    "reasoning": item.get("thinking"),
                    "methods": methods,
                    "temperature": item.get("temperature") is not None,
                    "max_temperature": item.get("maxTemperature"),
                    "text_generation": "candidate",
                    "adapter": "gemini_generate_content",
                },
                "capability_evidence": [{"source": "official", "confidence": "high"}],
                "catalog_metadata": item,
            }
        )
    return result


def _openrouter(source: dict[str, Any]) -> list[dict[str, Any]]:
    config = _base(source)
    query = {"output_modalities": source.get("output_modalities", "text")}
    if source.get("sort"):
        query["sort"] = source["sort"]
    if source.get("require_parameters"):
        query["supported_parameters"] = ",".join(source["require_parameters"])
    url = (
        config["base_url"].rstrip("/")
        + "/models?"
        + urllib.parse.urlencode(query, doseq=True)
    )
    payload = _get_json(url, config.get("api_key_env"), config.get("headers"))
    result = []
    for item in payload.get("data", []):
        parameters = item.get("supported_parameters") or []
        pricing = item.get("pricing") or {}
        prompt_price = _number(pricing.get("prompt"))
        completion_price = _number(pricing.get("completion"))
        architecture = item.get("architecture") or {}
        model = {
            "name": item.get("name", item["id"]),
            "provider": "openrouter",
            "model": item["id"],
            "created": item.get("created"),
            "context_length": item.get("context_length"),
            "capabilities": {
                "reasoning": "reasoning" in parameters,
                "structured_outputs": "structured_outputs" in parameters,
                "tools": "tools" in parameters,
                "input_modalities": architecture.get("input_modalities"),
                "output_modalities": architecture.get("output_modalities"),
                "supported_parameters": parameters,
                "temperature": "temperature" in parameters,
                "text_generation": "ready"
                if "text" in (architecture.get("output_modalities") or [])
                else None,
                "adapter": "openrouter_chat",
            },
            "capability_evidence": [{"source": "aggregator", "confidence": "high"}],
            "catalog_metadata": item,
        }
        if prompt_price is not None:
            model["input_cost_per_million"] = prompt_price * 1_000_000
        if completion_price is not None:
            model["output_cost_per_million"] = completion_price * 1_000_000
        if prompt_price is not None and completion_price is not None:
            model["pricing_metadata"] = {
                "source": "openrouter routed",
                "confidence": "authoritative",
            }
        result.append(model)
    return result


def _xai(source: dict[str, Any]) -> list[dict[str, Any]]:
    config = _base(source)
    result = []
    for endpoint, output_type in (
        ("language-models", None),
        ("image-generation-models", "image"),
        ("video-generation-models", "video"),
    ):
        payload = _get_json(
            config["base_url"].rstrip("/") + f"/{endpoint}",
            config.get("api_key_env"),
            config.get("headers"),
        )
        for item in payload.get("models", []):
            result.append(
                {
                    "name": item["id"],
                    "provider": "xai",
                    "model": item["id"],
                    "created": item.get("created"),
                    "capabilities": {
                        "input_modalities": item.get("input_modalities"),
                        "output_modalities": item.get("output_modalities")
                        or ([output_type] if output_type else None),
                        "fingerprint": item.get("fingerprint"),
                        "aliases": item.get("aliases", []),
                        "text_generation": "ready"
                        if "text" in (item.get("output_modalities") or [])
                        and not output_type
                        else None,
                        "adapter": "xai_chat",
                    },
                    "capability_evidence": [
                        {"source": "official", "confidence": "high"}
                    ],
                    "catalog_metadata": item,
                }
            )
    return result


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


DISCOVERERS = {
    "openai": _openai,
    "openai_compatible": _openai,
    "xai": _xai,
    "anthropic": _anthropic,
    "gemini": _gemini,
    "openrouter": _openrouter,
}


def discover_models(source: dict[str, Any]) -> list[dict[str, Any]]:
    provider = source.get("provider")
    if provider not in DISCOVERERS:
        raise ValueError(f"catalog discovery is unsupported for provider {provider!r}")
    if "limit" not in source or int(source["limit"]) < 1:
        raise ValueError("every discovery source requires a positive 'limit'")
    models = DISCOVERERS[provider](source)
    include = source.get("include")
    exclude = source.get("exclude")
    if include:
        models = [model for model in models if re.search(include, model["model"], re.I)]
    if exclude:
        models = [
            model for model in models if not re.search(exclude, model["model"], re.I)
        ]
    if not source.get("sort") and any(
        model.get("created") is not None for model in models
    ):
        models.sort(key=lambda model: str(model.get("created") or ""), reverse=True)
    inherited: dict[str, Any] = {
        key: source[key]
        for key in (
            "base_url",
            "api_key_env",
            "headers",
            "api_version",
        )
        if key in source
    }
    return [
        apply_public_pricing(classify_catalog_model({**model, **inherited}))
        for model in models[: int(source["limit"])]
    ]


def resolve_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    models = list(config.get("models", []))
    for source in config.get("discovery", []):
        models.extend(discover_models(source))
    seen: set[tuple[str, str]] = set()
    unique = []
    for model in models:
        identity = (model.get("provider", "openai_compatible"), model["model"])
        if identity not in seen:
            seen.add(identity)
            unique.append(apply_public_pricing(classify_catalog_model(model)))
    return _enrich_from_openrouter(unique)


def _enrich_from_openrouter(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use OpenRouter modality metadata as labelled enrichment for direct models."""
    aliases = {"xai": "x-ai", "gemini": "google"}
    router_models = {
        tuple(str(item["model"]).split("/", 1)): item
        for item in models
        if item.get("provider") == "openrouter" and "/" in str(item["model"])
    }
    normalized_router_models = {
        (author, _normalized_model_id(model_id)): item
        for (author, model_id), item in router_models.items()
    }
    enriched = []
    for model in models:
        provider = model.get("provider", "openai_compatible")
        author = aliases.get(provider, provider)
        candidate = router_models.get((author, str(model["model"])))
        match_source = "openrouter-exact"
        if candidate is None:
            candidate = normalized_router_models.get(
                (author, _normalized_model_id(str(model["model"])))
            )
            match_source = "openrouter-normalized"
        if provider == "openrouter" or candidate is None:
            enriched.append(model)
            continue
        router_capabilities = candidate.get("capabilities") or {}
        if not router_capabilities.get("output_modalities"):
            enriched.append(model)
            continue
        merged = dict(model)
        merged["capabilities"] = {
            **(model.get("capabilities") or {}),
            **{
                key: value
                for key, value in router_capabilities.items()
                if value is not None
            },
        }
        merged = classify_catalog_model(
            {key: value for key, value in merged.items() if key != "catalog_type"}
        )
        merged["catalog_confidence"] = "aggregator"
        merged["capability_evidence"] = [
            *(model.get("capability_evidence") or []),
            {"source": match_source, "confidence": "medium"},
        ]
        enriched.append(merged)
    return enriched


def _normalized_model_id(model_id: str) -> str:
    """Normalize provider spelling variants without fuzzy family matching."""
    return re.sub(r"(?<=\d)[.-](?=\d)", "", model_id.casefold())
