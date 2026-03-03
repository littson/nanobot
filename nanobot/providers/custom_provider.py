"""Direct OpenAI-compatible provider — bypasses LiteLLM."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import json_repair
from loguru import logger
from openai import AsyncOpenAI

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.utils.llm_metrics import log_llm_metrics


class CustomProvider(LLMProvider):
    @staticmethod
    def _normalize_model_name(model: str) -> str:
        return model.split("/", 1)[1] if model.startswith("gemini/") else model

    def __init__(
        self,
        api_key: str = "no-key",
        api_base: str = "http://localhost:8000/v1",
        default_model: str = "default",
        proxy: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = self._normalize_model_name(default_model)
        http_client: httpx.AsyncClient | None = None
        if proxy:
            http_client = httpx.AsyncClient(
                proxy=proxy,
                timeout=60.0,
                limits=httpx.Limits(max_keepalive_connections=0, max_connections=20),
            )
        self._client = AsyncOpenAI(api_key=api_key, base_url=api_base, http_client=http_client)

    @staticmethod
    def _is_retryable_connection_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        retry_signals = (
            "connection error",
            "apiconnectionerror",
            "remoteprotocolerror",
            "server disconnected",
            "read timeout",
            "timed out",
            "connection reset",
            "broken pipe",
        )
        return any(s in msg for s in retry_signals)

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                   tool_choice: str = "auto",
                   model: str | None = None, max_tokens: int = 4096, temperature: float = 0.7,
                   reasoning_effort: str | None = None) -> LLMResponse:
        started = time.perf_counter()
        requested_model = model or self.default_model
        kwargs: dict[str, Any] = {
            "model": self._normalize_model_name(requested_model),
            "messages": self._sanitize_empty_content(messages),
            "max_tokens": max(1, max_tokens),
            "temperature": temperature,
        }
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        if tools:
            kwargs.update(tools=tools, tool_choice=tool_choice)
        kwargs["extra_headers"] = {"Connection": "close"}

        attempts = 3
        for i in range(attempts):
            try:
                parsed = self._parse(await self._client.chat.completions.create(**kwargs))
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                log_llm_metrics({
                    "provider": "custom",
                    "model": requested_model,
                    "resolved_model": kwargs["model"],
                    "elapsed_ms": elapsed_ms,
                    "prompt_tokens": parsed.usage.get("prompt_tokens", 0),
                    "completion_tokens": parsed.usage.get("completion_tokens", 0),
                    "total_tokens": parsed.usage.get("total_tokens", 0),
                    "finish_reason": parsed.finish_reason,
                    "has_tools": bool(tools),
                    "tool_count": len(tools or []),
                    "message_count": len(messages),
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "reasoning_effort": reasoning_effort,
                    "error": False,
                    "attempts": i + 1,
                })
                return parsed
            except Exception as e:
                if i >= attempts - 1 or not self._is_retryable_connection_error(e):
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    log_llm_metrics({
                        "provider": "custom",
                        "model": requested_model,
                        "resolved_model": kwargs["model"],
                        "elapsed_ms": elapsed_ms,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "finish_reason": "error",
                        "has_tools": bool(tools),
                        "tool_count": len(tools or []),
                        "message_count": len(messages),
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "reasoning_effort": reasoning_effort,
                        "error": True,
                        "error_message": str(e),
                        "attempts": i + 1,
                    })
                    return LLMResponse(content=f"Error: {e}", finish_reason="error")
                backoff = 0.5 * (2 ** i)
                logger.warning(
                    "CustomProvider transient connection error (attempt {}/{}): {}. Retrying in {:.1f}s",
                    i + 1,
                    attempts,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log_llm_metrics({
            "provider": "custom",
            "model": requested_model,
            "resolved_model": kwargs["model"],
            "elapsed_ms": elapsed_ms,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "finish_reason": "error",
            "has_tools": bool(tools),
            "tool_count": len(tools or []),
            "message_count": len(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "reasoning_effort": reasoning_effort,
            "error": True,
            "error_message": "Connection error.",
            "attempts": attempts,
        })
        return LLMResponse(content="Error: Connection error.", finish_reason="error")

    def _parse(self, response: Any) -> LLMResponse:
        choice = response.choices[0]
        msg = choice.message
        tool_calls: list[ToolCallRequest] = []
        for tc in (msg.tool_calls or []):
            raw_tc = tc.model_dump(exclude_none=True) if hasattr(tc, "model_dump") else {
                "id": getattr(tc, "id", None),
                "type": getattr(tc, "type", "function"),
                "function": {
                    "name": getattr(getattr(tc, "function", None), "name", None),
                    "arguments": getattr(getattr(tc, "function", None), "arguments", "{}"),
                },
            }
            raw_args = raw_tc.get("function", {}).get("arguments", "{}")
            if isinstance(raw_args, str):
                args = json_repair.loads(raw_args)
            else:
                args = raw_args
                raw_tc["function"]["arguments"] = json.dumps(raw_args, ensure_ascii=False)

            tool_calls.append(ToolCallRequest(
                id=str(raw_tc.get("id") or getattr(tc, "id", "")),
                name=str(raw_tc.get("function", {}).get("name") or getattr(getattr(tc, "function", None), "name", "")),
                arguments=args,
                raw=raw_tc,
            ))
        u = response.usage
        return LLMResponse(
            content=msg.content, tool_calls=tool_calls, finish_reason=choice.finish_reason or "stop",
            usage={"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens, "total_tokens": u.total_tokens} if u else {},
            reasoning_content=getattr(msg, "reasoning_content", None) or None,
        )

    def get_default_model(self) -> str:
        return self.default_model
