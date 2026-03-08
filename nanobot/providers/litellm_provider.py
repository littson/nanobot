"""LiteLLM provider implementation for multi-provider support."""

import asyncio
import json
import os
import secrets
import string
import time
from typing import Any
from urllib.parse import urlparse

import json_repair
import litellm
from litellm import acompletion
from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.registry import find_by_model, find_gateway
from nanobot.utils.llm_metrics import extract_cached_tokens, log_llm_metrics

# Standard chat-completion message keys.
_ALLOWED_MSG_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content"})
_ANTHROPIC_EXTRA_KEYS = frozenset({"thinking_blocks"})
_ALNUM = string.ascii_letters + string.digits

def _short_tool_id() -> str:
    """Generate a 9-char alphanumeric ID compatible with all providers (incl. Mistral)."""
    return "".join(secrets.choice(_ALNUM) for _ in range(9))


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.
    
    Supports OpenRouter, Anthropic, OpenAI, Gemini, MiniMax, and many other providers through
    a unified interface.  Provider-specific logic is driven by the registry
    (see providers/registry.py) — no if-elif chains needed here.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
        proxy: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self.proxy = self._normalize_proxy(proxy)
        self._proxy_lock = asyncio.Lock()

        # Detect gateway / local deployment.
        # provider_name (from config key) is the primary signal;
        # api_key / api_base are fallback for auto-detection.
        self._gateway = find_gateway(provider_name, api_key, api_base)

        # Configure environment variables
        if api_key:
            self._setup_env(api_key, api_base, default_model)

        if api_base:
            litellm.api_base = api_base

        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True
        # Drop unsupported parameters for providers (e.g., gpt-5 rejects some params)
        litellm.drop_params = True

    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """Set environment variables based on detected provider."""
        spec = self._gateway or find_by_model(model)
        if not spec:
            return
        if not spec.env_key:
            # OAuth/provider-only specs (for example: openai_codex)
            return

        # Gateway/local overrides existing env; standard provider doesn't
        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        # Resolve env_extras placeholders:
        #   {api_key}  → user's API key
        #   {api_base} → user's api_base, falling back to spec.default_api_base
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider/gateway prefixes."""
        if self._gateway:
            # Gateway mode: apply gateway prefix, skip provider-specific prefixes
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model

        # Standard mode: auto-prefix for known providers
        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            model = self._canonicalize_explicit_prefix(model, spec.name, spec.litellm_prefix)
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"

        return model

    @staticmethod
    def _canonicalize_explicit_prefix(model: str, spec_name: str, canonical_prefix: str) -> str:
        """Normalize explicit provider prefixes like `github-copilot/...`."""
        if "/" not in model:
            return model
        prefix, remainder = model.split("/", 1)
        if prefix.lower().replace("-", "_") != spec_name:
            return model
        return f"{canonical_prefix}/{remainder}"

    @staticmethod
    def _normalize_proxy(proxy: str | None) -> str | None:
        """Normalize proxy URL for better cross-library compatibility."""
        if not proxy:
            return None
        raw = proxy.strip()
        if not raw:
            return None
        return raw

    @staticmethod
    def _is_retryable_connection_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        retry_signals = (
            "server disconnected",
            "remoteprotocolerror",
            "apiconnectionerror",
            "readerror",
            "connection reset",
            "temporarily unavailable",
            "timed out",
            "timeout",
        )
        return any(signal in msg for signal in retry_signals)

    def _supports_cache_control(self, model: str) -> bool:
        """Return True when the provider supports cache_control on content blocks."""
        if self._gateway is not None:
            return self._gateway.supports_prompt_caching
        spec = find_by_model(model)
        return spec is not None and spec.supports_prompt_caching

    def _apply_cache_control(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Return copies of messages and tools with cache_control injected."""
        new_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg["content"]
                if isinstance(content, str):
                    new_content = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
                else:
                    new_content = list(content)
                    new_content[-1] = {**new_content[-1], "cache_control": {"type": "ephemeral"}}
                new_messages.append({**msg, "content": new_content})
            else:
                new_messages.append(msg)

        new_tools = tools
        if tools:
            new_tools = list(tools)
            new_tools[-1] = {**new_tools[-1], "cache_control": {"type": "ephemeral"}}

        return new_messages, new_tools

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from the registry."""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return

    @staticmethod
    def _extra_msg_keys(original_model: str, resolved_model: str) -> frozenset[str]:
        """Return provider-specific extra keys to preserve in request messages."""
        spec = find_by_model(original_model) or find_by_model(resolved_model)
        if (spec and spec.name == "anthropic") or "claude" in original_model.lower() or resolved_model.startswith("anthropic/"):
            return _ANTHROPIC_EXTRA_KEYS
        return frozenset()

    @staticmethod
    def _sanitize_messages(messages: list[dict[str, Any]], extra_keys: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
        """Strip non-standard keys and ensure assistant messages have a content key."""
        allowed = _ALLOWED_MSG_KEYS | extra_keys
        sanitized = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in allowed}
            # Strict providers require "content" even when assistant only has tool_calls
            if clean.get("role") == "assistant" and "content" not in clean:
                clean["content"] = None
            sanitized.append(clean)
        return sanitized

    @staticmethod
    def _debug_message_roles(messages: list[dict[str, Any]]) -> list[str]:
        """Build compact role trace for upstream role-validation debugging."""
        role_trace: list[str] = []
        for idx, msg in enumerate(messages):
            role = str(msg.get("role", "<missing>"))
            flags: list[str] = []
            if msg.get("tool_calls"):
                flags.append("tool_calls")
            if msg.get("tool_call_id"):
                flags.append("tool_call_id")
            suffix = f" ({','.join(flags)})" if flags else ""
            role_trace.append(f"{idx}:{role}{suffix}")
        return role_trace

    @staticmethod
    def _debug_messages_json(messages: list[dict[str, Any]]) -> str:
        """Serialize request messages for direct upstream debugging."""
        try:
            return json.dumps(messages, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            return str(messages)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request via LiteLLM.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        started = time.perf_counter()
        original_model = model or self.default_model
        model = self._resolve_model(original_model)
        extra_msg_keys = self._extra_msg_keys(original_model, model)

        if self._supports_cache_control(original_model):
            messages, tools = self._apply_cache_control(messages, tools)

        # Clamp max_tokens to at least 1 — negative or zero values cause
        # LiteLLM to reject the request with "max_tokens must be at least 1".
        max_tokens = max(1, max_tokens)

        sanitized_messages = self._sanitize_messages(self._sanitize_empty_content(messages), extra_keys=extra_msg_keys)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": sanitized_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Apply model-specific overrides (e.g. kimi-k2.5 temperature)
        self._apply_model_overrides(model, kwargs)

        # Pass api_key directly — more reliable than env vars alone
        if self.api_key:
            kwargs["api_key"] = self.api_key

        # Pass api_base for custom endpoints
        if self.api_base:
            kwargs["api_base"] = self.api_base

        # Pass extra headers (e.g. APP-Code for AiHubMix)
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
            kwargs["drop_params"] = True
        
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        try:
            response = await self._acompletion_with_proxy(**kwargs)
            parsed = self._parse_response(response)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log_llm_metrics({
                "provider": "litellm",
                "provider_name": self._gateway.name if self._gateway else (find_by_model(original_model).name if find_by_model(original_model) else ""),
                "model": original_model,
                "resolved_model": model,
                "elapsed_ms": elapsed_ms,
                "prompt_tokens": parsed.usage.get("prompt_tokens", 0),
                "completion_tokens": parsed.usage.get("completion_tokens", 0),
                "total_tokens": parsed.usage.get("total_tokens", 0),
                "cached_tokens": parsed.usage.get("cached_tokens", 0),
                "finish_reason": parsed.finish_reason,
                "has_tools": bool(tools),
                "tool_count": len(tools or []),
                "message_count": len(messages),
                "max_tokens": max_tokens,
                "temperature": temperature,
                "reasoning_effort": reasoning_effort,
                "error": False,
            })
            return parsed
        except Exception as e:
            is_gemini_request = "gemini" in model.lower() or "gemini" in original_model.lower()
            role_trace = self._debug_message_roles(sanitized_messages) if is_gemini_request else []
            messages_json = self._debug_messages_json(sanitized_messages) if is_gemini_request else ""
            if is_gemini_request:
                logger.warning(
                    "Gemini request failed. model={} roles_sent={} messages_sent_json={} error={}",
                    model,
                    role_trace,
                    messages_json,
                    e,
                )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log_llm_metrics({
                "provider": "litellm",
                "provider_name": self._gateway.name if self._gateway else (find_by_model(original_model).name if find_by_model(original_model) else ""),
                "model": original_model,
                "resolved_model": model,
                "elapsed_ms": elapsed_ms,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": 0,
                "finish_reason": "error",
                "has_tools": bool(tools),
                "tool_count": len(tools or []),
                "message_count": len(messages),
                "max_tokens": max_tokens,
                "temperature": temperature,
                "reasoning_effort": reasoning_effort,
                "error": True,
                "error_message": str(e),
            })
            # Return error as content for graceful handling
            error_text = f"Error calling LLM: {str(e)}"
            if is_gemini_request:
                error_text = f"{error_text}\nroles_sent={role_trace}\nmessages_sent_json={messages_json}"
            return LLMResponse(
                content=error_text,
                finish_reason="error",
            )

    async def _acompletion_with_proxy(self, **kwargs: Any):
        """Run LiteLLM request with optional temporary proxy env vars."""
        if not self.proxy:
            return await acompletion(**kwargs)
        parsed = urlparse(self.proxy)
        is_socks = parsed.scheme.lower().startswith("socks")

        # Some Gemini/LiteLLM paths only honor a subset of env vars.
        # Try multiple env profiles in sequence.
        profiles: list[tuple[str, tuple[str, ...]]] = []
        if is_socks:
            profiles = [
                ("all_proxy_only", ("ALL_PROXY", "all_proxy")),
                ("https_http", ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")),
                ("all", ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")),
            ]
        else:
            profiles = [
                ("https_http", ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")),
                ("all", ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")),
            ]

        attempts = 3
        last_err: Exception | None = None
        async with self._proxy_lock:
            logger.info("LiteLLM request via proxy: {}", self.proxy)
            for profile_name, proxy_keys in profiles:
                prev_env = {k: os.environ.get(k) for k in proxy_keys}
                try:
                    for k in proxy_keys:
                        os.environ[k] = self.proxy

                    for i in range(attempts):
                        try:
                            return await acompletion(**kwargs)
                        except Exception as e:
                            last_err = e
                            if i >= attempts - 1 or not self._is_retryable_connection_error(e):
                                break
                            backoff = 0.4 * (2 ** i)
                            logger.warning(
                                "LiteLLM connection error via proxy [{}] (attempt {}/{}): {}. Retrying in {:.1f}s",
                                profile_name,
                                i + 1,
                                attempts,
                                e,
                                backoff,
                            )
                            await asyncio.sleep(backoff)
                finally:
                    for k, v in prev_env.items():
                        if v is None:
                            os.environ.pop(k, None)
                        else:
                            os.environ[k] = v

                if last_err and not self._is_retryable_connection_error(last_err):
                    raise last_err
                logger.warning("LiteLLM proxy profile [{}] failed, trying next profile", profile_name)

        if last_err:
            raise last_err
        raise RuntimeError("LiteLLM proxy request failed without specific exception")

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM response into our standard format."""
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # Parse arguments from JSON string if needed
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json_repair.loads(args)

                tool_calls.append(ToolCallRequest(
                    id=_short_tool_id(),
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "cached_tokens": extract_cached_tokens(response.usage),
            }

        reasoning_content = getattr(message, "reasoning_content", None) or None
        thinking_blocks = getattr(message, "thinking_blocks", None) or None
        
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
        )

    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
