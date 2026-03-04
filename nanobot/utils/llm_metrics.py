"""Helpers for writing per-call LLM metrics to a JSONL log file."""

from __future__ import annotations

import json
import os
import threading
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_METRICS_PATH_ENV = "NANOBOT_LLM_METRICS_FILE"
_DEFAULT_METRICS_PATH = Path.home() / ".nanobot" / "logs" / "llm_metrics.jsonl"
_PROVIDER_ALIASES = {
    "vertex_native": "vertex",
    "vertex_openai": "vertex",
    "openai-codex": "openai_codex",
    "github-copilot": "github_copilot",
}
_COMMON_PROVIDER_PREFIXES = {
    "aihubmix",
    "anthropic",
    "custom",
    "dashscope",
    "deepseek",
    "gemini",
    "github_copilot",
    "google",
    "groq",
    "hosted_vllm",
    "minimax",
    "moonshot",
    "openai",
    "openai_codex",
    "openrouter",
    "siliconflow",
    "vertex",
    "vllm",
    "volcengine",
    "zai",
    "zhipu",
}


def _metrics_path() -> Path:
    raw = os.environ.get(_METRICS_PATH_ENV, "").strip()
    return Path(raw).expanduser() if raw else _DEFAULT_METRICS_PATH


def get_llm_metrics_path() -> Path:
    """Return the current LLM metrics JSONL path."""
    return _metrics_path()


def _normalize_provider_token(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _lookup_value(obj: object, key: str) -> object:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def extract_cached_tokens(usage: object) -> int:
    """Best-effort extraction of cached prompt tokens from usage payloads."""
    if usage is None:
        return 0

    # Explicit fields used by some SDKs/providers.
    direct_keys = (
        "cached_tokens",
        "cachedTokens",
        "cache_read_input_tokens",
        "cacheReadInputTokens",
        "cachedContentTokenCount",
    )
    for key in direct_keys:
        value = _lookup_value(usage, key)
        if value is not None:
            parsed = _as_int(value)
            if parsed > 0:
                return parsed

    # Nested token-details fields commonly returned by OpenAI-compatible SDKs.
    detail_keys = (
        "prompt_tokens_details",
        "prompt_token_details",
        "input_tokens_details",
        "promptTokensDetails",
        "promptTokenDetails",
        "inputTokensDetails",
    )
    for parent in detail_keys:
        details = _lookup_value(usage, parent)
        if details is None:
            continue
        for child in ("cached_tokens", "cachedTokens"):
            parsed = _as_int(_lookup_value(details, child))
            if parsed > 0:
                return parsed

    return 0


@lru_cache(maxsize=1)
def _registry_helpers():
    try:
        from nanobot.providers.registry import find_by_model, find_by_name
    except Exception:
        return None, None
    return find_by_model, find_by_name


def _canonical_provider_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""

    token = _normalize_provider_token(value)
    token = _PROVIDER_ALIASES.get(token, token)
    _, find_by_name = _registry_helpers()
    if find_by_name:
        spec = find_by_name(token)
        if spec:
            return spec.name

    if token.endswith("_provider"):
        if find_by_name:
            spec = find_by_name(token[:-9])
            if spec:
                return spec.name

    return token


def _infer_provider_from_model(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""

    model = value.strip()
    prefix = _normalize_provider_token(model.split("/", 1)[0])
    find_by_model, find_by_name = _registry_helpers()
    if find_by_name:
        spec = find_by_name(prefix)
        if spec:
            return spec.name
    elif prefix in _COMMON_PROVIDER_PREFIXES:
        if prefix == "google" and "gemini" in _normalize_provider_token(model):
            return "gemini"
        return _PROVIDER_ALIASES.get(prefix, prefix)

    if find_by_model:
        spec = find_by_model(model)
        if spec:
            return spec.name
    return ""


def resolve_provider_name(record: dict[str, Any]) -> str:
    """Resolve canonical provider name for one metrics record."""
    explicit = _canonical_provider_name(record.get("provider_name"))
    if explicit:
        return explicit

    backend = _canonical_provider_name(record.get("provider"))
    if backend and backend != "litellm":
        return backend

    for field in ("resolved_model", "model"):
        inferred = _infer_provider_from_model(record.get(field))
        if inferred:
            return inferred

    return backend


def log_llm_metrics(record: dict[str, Any]) -> None:
    """Append one metrics record as a JSON line.

    Best-effort only: this function must never break request flow.
    """
    try:
        normalized = dict(record)
        provider_name = resolve_provider_name(normalized)
        if provider_name:
            normalized["provider_name"] = provider_name

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **normalized,
        }
        path = _metrics_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        return
