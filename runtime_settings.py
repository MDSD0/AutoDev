"""
runtime_settings.py — persisted runtime settings for AutoDev.

These settings are stored locally on the machine running the app so the
frontend settings page can control provider keys, local model endpoints,
and default orchestration behavior without requiring preloaded shared keys.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime
from typing import Any

from .config import Config
from .request_context import allow_server_secrets, get_runtime_override

_ROOT = os.path.dirname(os.path.abspath(__file__))
RUNTIME_SETTINGS_PATH = os.path.join(_ROOT, ".autodev_runtime_settings.json")
_VALID_DEFAULT_PROVIDERS = {"auto", "gemini", "groq", "groq_2", "ollama", "custom"}


def _configured_default_provider() -> str:
    return Config.DEFAULT_PROVIDER if Config.DEFAULT_PROVIDER in _VALID_DEFAULT_PROVIDERS else "auto"


def _defaults() -> dict[str, Any]:
    return {
        "version": 2,
        "updatedAt": None,
        "workflow_defaults": {
            "defaultProvider": _configured_default_provider(),
            "workflowDepth": "balanced",
            "mode": "auto",
            "refinePrompt": True,
            "maxRetries": Config.MAX_RETRIES,
            "expertMode": False,
            "skipSpec": False,
            "skipReview": False,
        },
        "provider_registry": {
            "defaultProvider": _configured_default_provider(),
            "providers": {
                "auto": {"enabled": True, "selectedModel": "auto"},
                "gemini": {"enabled": True, "selectedModel": "gemini"},
                "groq": {"enabled": True, "selectedModel": "groq"},
                "groq_2": {"enabled": True, "selectedModel": "groq_2"},
                "ollama": {"enabled": True, "selectedModel": "ollama"},
                "custom": {"enabled": True, "selectedModel": "custom"},
            },
            "providerStates": {},
            "roleAssignments": {
                "router": {"provider": "auto", "model": "auto"},
                "planner": {"provider": "gemini", "model": "gemini"},
                "coder": {"provider": "groq", "model": "groq"},
                "reviewer": {"provider": "auto", "model": "auto"},
                "executor": {"provider": "groq", "model": "groq"},
            },
        },
        "secrets": {
            "geminiApiKey": "",
            "groqApiKey": "",
            "groqApiKey2": "",
            "huggingFaceApiKey": "",
            "ollamaBaseUrl": Config.OLLAMA_BASE_URL,
        },
        "local_models": {
            "sourceType": "ollama",
            "ollamaModel": Config.OLLAMA_MODEL,
            "ollamaBaseUrl": Config.OLLAMA_BASE_URL,
            "huggingFaceUrl": "",
            "modelFilePath": "",
            "llamaCppCommand": "llama-server",
            "llamaCppPort": 8001,
            "llamaCppContext": 4096,
            "llamaCppPid": None,
            "llamaCppStatus": "stopped",
            "hfDownloadPattern": "*.gguf",
            "hfLocalDir": os.path.join(_ROOT, "coding", "local_models"),
            "localEndpointUrl": "",
            "localEndpointModel": "local-model",
            "localEndpointApiKey": "",
            "localEndpointAuthHeader": "Authorization",
            "selectedLocalProvider": "ollama",
        },
        "customEndpoints": [],
        "setupCompleted": False,
        "setupCompletedAt": None,
        "desktopMode": os.getenv("AUTODEV_DESKTOP") == "1",
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _coerce_runtime_shape(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Accept both frontend camelCase and backend snake_case settings."""
    incoming = copy.deepcopy(settings or {})

    workflow_defaults = incoming.get("workflow_defaults", {})
    if isinstance(incoming.get("workflowDefaults"), dict):
        workflow_defaults = _deep_merge(
            workflow_defaults if isinstance(workflow_defaults, dict) else {},
            incoming.pop("workflowDefaults"),
        )
    if workflow_defaults:
        incoming["workflow_defaults"] = workflow_defaults

    provider_registry = incoming.get("provider_registry", {})
    if isinstance(incoming.get("providerRegistry"), dict):
        provider_registry = _deep_merge(
            provider_registry if isinstance(provider_registry, dict) else {},
            incoming.pop("providerRegistry"),
        )
    if provider_registry:
        incoming["provider_registry"] = provider_registry

    if isinstance(incoming.get("localModels"), dict):
        incoming["local_models"] = _deep_merge(incoming.get("local_models", {}), incoming.pop("localModels"))

    return incoming


def _normalize_runtime_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    normalized = _deep_merge(_defaults(), _coerce_runtime_shape(settings))

    workflow = normalized.setdefault("workflow_defaults", {})
    registry = normalized.setdefault("provider_registry", {})

    default_provider = registry.get("defaultProvider") or workflow.get("defaultProvider") or _configured_default_provider()
    if default_provider not in _VALID_DEFAULT_PROVIDERS:
        default_provider = "auto"
    registry["defaultProvider"] = default_provider
    workflow["defaultProvider"] = default_provider
    workflow["maxRetries"] = max(1, min(6, int(workflow.get("maxRetries", Config.MAX_RETRIES))))

    local = normalized.setdefault("local_models", {})
    local["ollamaBaseUrl"] = local.get("ollamaBaseUrl") or normalized.get("secrets", {}).get("ollamaBaseUrl") or Config.OLLAMA_BASE_URL
    local["ollamaModel"] = local.get("ollamaModel") or Config.OLLAMA_MODEL
    local["llamaCppPort"] = int(local.get("llamaCppPort") or 8001)
    local["llamaCppContext"] = int(local.get("llamaCppContext") or 4096)
    local["localEndpointAuthHeader"] = local.get("localEndpointAuthHeader") or "Authorization"
    local["hfDownloadPattern"] = local.get("hfDownloadPattern") or "*.gguf"
    local["hfLocalDir"] = os.path.expanduser(
        local.get("hfLocalDir") or os.path.join(_ROOT, "coding", "local_models")
    )

    normalized.pop("workflowDefaults", None)
    normalized.pop("providerRegistry", None)
    normalized.pop("localModels", None)
    return normalized


def redact_runtime_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    normalized = _normalize_runtime_settings(settings)
    secrets = normalized.setdefault("secrets", {})
    secrets["geminiApiKey"] = ""
    secrets["groqApiKey"] = ""
    secrets["groqApiKey2"] = ""
    secrets["huggingFaceApiKey"] = ""

    local = normalized.setdefault("local_models", {})
    local["localEndpointApiKey"] = ""

    endpoints = normalized.get("customEndpoints", [])
    if isinstance(endpoints, list):
        for endpoint in endpoints:
            if isinstance(endpoint, dict):
                endpoint["apiKey"] = ""

    return normalized


def _load_persisted_runtime_settings() -> dict[str, Any]:
    defaults = _defaults()
    if not os.path.exists(RUNTIME_SETTINGS_PATH):
        return defaults
    try:
        with open(RUNTIME_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _normalize_runtime_settings(data)
    except Exception:
        return defaults


def load_runtime_settings() -> dict[str, Any]:
    persisted = _load_persisted_runtime_settings()
    override = get_runtime_override()
    if isinstance(override, dict):
        return _normalize_runtime_settings(_deep_merge(persisted, override))
    return persisted


def save_runtime_settings(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_runtime_settings(settings)
    stored = redact_runtime_settings(normalized)
    stored["updatedAt"] = datetime.utcnow().isoformat() + "Z"
    os.makedirs(os.path.dirname(RUNTIME_SETTINGS_PATH), exist_ok=True)
    with open(RUNTIME_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(stored, f, indent=2)
    return stored


def get_provider_secret(provider: str, settings: dict[str, Any] | None = None) -> str:
    s = settings or load_runtime_settings()
    secrets = s.get("secrets", {})
    allow_fallback = allow_server_secrets()
    if provider == "gemini":
        return secrets.get("geminiApiKey") or (Config.GOOGLE_API_KEY if allow_fallback else "")
    if provider == "groq":
        return secrets.get("groqApiKey") or (Config.GROQ_API_KEY if allow_fallback else "")
    if provider == "groq_2":
        return secrets.get("groqApiKey2") or (Config.GROQ_API_KEY_2 if allow_fallback else "")
    if provider == "huggingface":
        return secrets.get("huggingFaceApiKey", "")
    return ""


def get_ollama_runtime(settings: dict[str, Any] | None = None) -> tuple[str, str]:
    s = settings or load_runtime_settings()
    local = s.get("local_models", {})
    model = local.get("ollamaModel") or Config.OLLAMA_MODEL
    base_url = local.get("ollamaBaseUrl") or s.get("secrets", {}).get("ollamaBaseUrl") or Config.OLLAMA_BASE_URL
    return model, base_url


def get_custom_runtime(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    s = settings or load_runtime_settings()
    local = s.get("local_models", {})
    return {
        "endpoint_url": local.get("localEndpointUrl", "").strip(),
        "model": local.get("localEndpointModel", "").strip() or "local-model",
        "api_key": local.get("localEndpointApiKey", "").strip(),
        "auth_header": local.get("localEndpointAuthHeader", "").strip() or "Authorization",
    }
