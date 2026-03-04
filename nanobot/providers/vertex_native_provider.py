"""Vertex AI native generateContent provider."""

from __future__ import annotations

import asyncio
import json
import time
from copy import deepcopy
from typing import Any

import httpx
import json_repair
from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.utils.llm_metrics import extract_cached_tokens, log_llm_metrics


class VertexNativeProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        api_base: str,
        default_model: str = "google/gemini-2.5-flash",
        proxy: str | None = None,
        extra_headers: dict[str, str] | None = None,
        auth_mode: str = "oauth_bearer",  # oauth_bearer / api_key_query
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self.auth_mode = auth_mode
        self._client = httpx.AsyncClient(
            proxy=proxy,
            timeout=60.0,
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=20),
        )

    @staticmethod
    def _is_retryable_connection_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        retry_signals = (
            "connection error",
            "remoteprotocolerror",
            "server disconnected",
            "read timeout",
            "timed out",
            "connection reset",
            "broken pipe",
        )
        return any(s in msg for s in retry_signals)

    @staticmethod
    def _resolve_model_name(model: str) -> str:
        # Vertex native expects names like "gemini-2.5-flash", not "google/gemini-2.5-flash".
        for prefix in ("google/", "gemini/"):
            if model.startswith(prefix):
                return model.split("/", 1)[1]
        return model

    def _build_url(self, model: str) -> str:
        base = (self.api_base or "").rstrip("/")
        if base.endswith(":generateContent"):
            return base
        return f"{base}/{model}:generateContent"

    @staticmethod
    def _text_parts_from_content(content: Any) -> list[dict[str, Any]]:
        if isinstance(content, str):
            return [{"text": content}]
        if isinstance(content, list):
            parts: list[dict[str, Any]] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") in ("text", "input_text", "output_text"):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append({"text": text})
            return parts
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                return [{"text": text}]
        return []

    @staticmethod
    def _parse_tool_arguments(raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                fixed = json_repair.loads(raw_args)
                return fixed if isinstance(fixed, dict) else {}
            except Exception:
                return {}
        return {}

    @staticmethod
    def _tool_response_payload(content: Any) -> dict[str, Any]:
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            txt = content.strip()
            if not txt:
                return {"output": ""}
            try:
                parsed = json_repair.loads(txt)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            return {"output": content}
        if content is None:
            return {"output": None}
        return {"output": content}

    def _convert_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        system_instruction: dict[str, Any] | None = None
        contents: list[dict[str, Any]] = []
        pending_tool_parts: list[dict[str, Any]] = []
        pending_tool_expected = 0
        pending_tool_count = 0

        def _flush_pending_tool_parts() -> None:
            nonlocal pending_tool_parts, pending_tool_expected, pending_tool_count
            if pending_tool_parts:
                contents.append({"role": "user", "parts": pending_tool_parts})
                pending_tool_parts = []
            pending_tool_expected = 0
            pending_tool_count = 0

        def _build_function_response_part(tool_msg: dict[str, Any]) -> dict[str, Any]:
            name = tool_msg.get("name") or "tool"
            response_obj = self._tool_response_payload(tool_msg.get("content"))
            return {"functionResponse": {"name": name, "response": response_obj}}

        for msg in self._sanitize_empty_content(messages):
            role = msg.get("role")
            content = msg.get("content")

            if role == "system":
                _flush_pending_tool_parts()
                parts = self._text_parts_from_content(content)
                if parts:
                    system_instruction = {"role": "system", "parts": parts}
                continue

            if role == "user":
                _flush_pending_tool_parts()
                parts = self._text_parts_from_content(content)
                if parts:
                    contents.append({"role": "user", "parts": parts})
                continue

            if role == "assistant":
                _flush_pending_tool_parts()
                parts: list[dict[str, Any]] = []
                parts.extend(self._text_parts_from_content(content))
                assistant_tool_calls = msg.get("tool_calls", []) or []
                for tc in assistant_tool_calls:
                    # Preserve the original Vertex functionCall part (including thought signature)
                    # when available, to satisfy strict validator requirements.
                    extra = tc.get("extra_content") if isinstance(tc, dict) else None
                    google_extra = (extra or {}).get("google") if isinstance(extra, dict) else None
                    original_part = (google_extra or {}).get("vertex_function_call_part") if isinstance(google_extra, dict) else None
                    if isinstance(original_part, dict):
                        parts.append(deepcopy(original_part))
                        continue

                    fn = tc.get("function") or {}
                    name = fn.get("name")
                    if not name:
                        continue
                    args = self._parse_tool_arguments(fn.get("arguments", "{}"))
                    parts.append({"functionCall": {"name": name, "args": args}})
                if parts:
                    contents.append({"role": "model", "parts": parts})
                if assistant_tool_calls:
                    pending_tool_expected = len(assistant_tool_calls)
                    pending_tool_count = 0
                    pending_tool_parts = []
                continue

            if role == "tool":
                part = _build_function_response_part(msg)
                if pending_tool_expected > 0:
                    pending_tool_parts.append(part)
                    pending_tool_count += 1
                    if pending_tool_count >= pending_tool_expected:
                        _flush_pending_tool_parts()
                else:
                    contents.append({"role": "user", "parts": [part]})

        _flush_pending_tool_parts()
        return system_instruction, contents

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        declarations: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            fn = tool.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            declarations.append({
                "name": name,
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object"},
            })
        return [{"functionDeclarations": declarations}] if declarations else None

    @staticmethod
    def _convert_tool_choice(tool_choice: str) -> dict[str, Any]:
        mode_map = {"auto": "AUTO", "none": "NONE", "required": "ANY"}
        return {"functionCallingConfig": {"mode": mode_map.get(tool_choice, "AUTO")}}

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        system_instruction, contents = self._convert_messages(messages)
        body: dict[str, Any] = {
            "contents": contents if contents else [{"role": "user", "parts": [{"text": "(empty)"}]}],
            "generationConfig": {
                "maxOutputTokens": max(1, max_tokens),
                "temperature": temperature,
            },
        }
        if system_instruction:
            body["systemInstruction"] = system_instruction

        native_tools = self._convert_tools(tools)
        if native_tools:
            body["tools"] = native_tools
            body["toolConfig"] = self._convert_tool_choice(tool_choice)
        return body

    @staticmethod
    def _finish_reason(candidate: dict[str, Any] | None) -> str:
        if not candidate:
            return "stop"
        raw = (candidate.get("finishReason") or "STOP").lower()
        mapping = {
            "stop": "stop",
            "max_tokens": "length",
            "max_output_tokens": "length",
            "safety": "content_filter",
        }
        return mapping.get(raw, raw)

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> LLMResponse:
        candidates = data.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        content = (candidate.get("content") or {}) if isinstance(candidate, dict) else {}
        parts = content.get("parts") or []

        text_chunks: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        tool_idx = 0

        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                text_chunks.append(text)
            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                name = function_call.get("name")
                if not name:
                    continue
                args = function_call.get("args") if isinstance(function_call.get("args"), dict) else {}
                call_id = str(function_call.get("id") or f"call_{tool_idx}")
                tool_idx += 1
                raw = {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                    "extra_content": {
                        "google": {
                            # Keep the full original part so thought signatures can be replayed verbatim.
                            "vertex_function_call_part": deepcopy(part),
                        }
                    },
                }
                tool_calls.append(
                    ToolCallRequest(
                        id=call_id,
                        name=str(name),
                        arguments=args,
                        raw=raw,
                    )
                )

        usage_md = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
        usage = {
            "prompt_tokens": int(usage_md.get("promptTokenCount", 0) or 0),
            "completion_tokens": int(usage_md.get("candidatesTokenCount", 0) or 0),
            "total_tokens": int(usage_md.get("totalTokenCount", 0) or 0),
            "cached_tokens": extract_cached_tokens(usage_md),
        }
        return LLMResponse(
            content="".join(text_chunks) if text_chunks else None,
            tool_calls=tool_calls,
            finish_reason=VertexNativeProvider._finish_reason(candidate if isinstance(candidate, dict) else None),
            usage=usage,
        )

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
        started = time.perf_counter()
        requested_model = model or self.default_model
        resolved_model = self._resolve_model_name(requested_model)
        url = self._build_url(resolved_model)
        payload = self._build_payload(messages, tools, tool_choice, max_tokens=max_tokens, temperature=temperature)

        headers = {
            "Content-Type": "application/json",
            "Connection": "close",
            **self.extra_headers,
        }
        params: dict[str, str] | None = None
        if self.auth_mode == "api_key_query":
            params = {"key": self.api_key}
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"

        attempts = 3
        for i in range(attempts):
            try:
                resp = await self._client.post(url, headers=headers, params=params, json=payload)
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

                parsed = self._parse_response(resp.json())
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                log_llm_metrics({
                    "provider": "vertex_native",
                    "provider_name": "vertex",
                    "model": requested_model,
                    "resolved_model": resolved_model,
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
                    "attempts": i + 1,
                })
                return parsed
            except Exception as e:
                if i >= attempts - 1 or not self._is_retryable_connection_error(e):
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    log_llm_metrics({
                        "provider": "vertex_native",
                        "provider_name": "vertex",
                        "model": requested_model,
                        "resolved_model": resolved_model,
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
                        "attempts": i + 1,
                    })
                    return LLMResponse(content=f"Error: {e}", finish_reason="error")
                backoff = 0.5 * (2 ** i)
                logger.warning(
                    "VertexNativeProvider transient connection error (attempt {}/{}): {}. Retrying in {:.1f}s",
                    i + 1,
                    attempts,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log_llm_metrics({
            "provider": "vertex_native",
            "provider_name": "vertex",
            "model": requested_model,
            "resolved_model": resolved_model,
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
            "error_message": "Connection error.",
            "attempts": attempts,
        })
        return LLMResponse(content="Error: Connection error.", finish_reason="error")

    def get_default_model(self) -> str:
        return self.default_model
