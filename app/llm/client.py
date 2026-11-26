"""Minimal OpenAI-compatible chat client with tool calling and usage tracking.

Uses only the standard library, so it works against Groq, OpenAI, Gemini's
OpenAI endpoint or Ollama without an SDK dependency.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.settings import Settings, get_settings


class LLMError(RuntimeError):
    """The model could not be reached or returned an unusable response."""


class ToolCallRejected(LLMError):
    """The provider rejected the model's own tool call (e.g. schema mismatch). Repairable."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    raw_message: dict[str, Any] = field(default_factory=dict)


class ChatModel(Protocol):
    model: str

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.0, max_tokens: int = 2000) -> LLMResponse: ...


class OpenAICompatibleClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.llm_model

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.0, max_tokens: int = 2000) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.settings.llm_reasoning_effort and "gpt-oss" in self.model:
            # Reasoning tokens count toward max_tokens; low effort keeps calls fast and cheap.
            payload["reasoning_effort"] = self.settings.llm_reasoning_effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # A real User-Agent: some providers sit behind Cloudflare, which rejects
        # the default "Python-urllib" agent with HTTP 403.
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "ai-analytics-copilot/0.2 (+https://github.com/Gauravkumar45/AI-Analytics-Copilot)"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        request = urllib.request.Request(
            self.settings.llm_base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        started = time.perf_counter()
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.settings.llm_timeout_seconds) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < self.settings.llm_max_retries:
                    time.sleep(_retry_after(exc, attempt))
                    continue
                self._raise_http(exc)
            except (urllib.error.URLError, TimeoutError) as exc:
                raise LLMError(f"Could not reach the LLM at {self.settings.llm_base_url}: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000
        return self._parse(body, latency_ms)

    def _raise_http(self, exc: urllib.error.HTTPError) -> None:
        detail = exc.read().decode("utf-8", errors="ignore")[:600]
        if exc.code == 400 and "tool_use_failed" in detail:
            raise ToolCallRejected(f"Invalid tool call: {_error_message(detail)}") from exc
        if exc.code == 401:
            raise LLMError("LLM API key is missing or invalid (set GROQ_API_KEY or LLM_API_KEY)") from exc
        if exc.code == 403:
            raise LLMError(f"LLM request forbidden (HTTP 403): {detail}") from exc
        if exc.code == 404:
            raise LLMError(f"Model '{self.model}' not found at {self.settings.llm_base_url}: {detail}") from exc
        if exc.code == 429:
            raise LLMError("LLM rate limit reached; try again in a minute") from exc
        raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc

    def _parse(self, body: dict, latency_ms: float) -> LLMResponse:
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError("LLM returned no choices") from exc

        calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": fn.get("arguments")}
            calls.append(ToolCall(id=call.get("id", ""), name=fn.get("name", ""), arguments=args))

        usage = body.get("usage") or {}
        return LLMResponse(
            content=message.get("content"),
            tool_calls=calls,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=latency_ms,
            raw_message=message,
        )


def _error_message(detail: str) -> str:
    try:
        return json.loads(detail)["error"]["message"][:300]
    except (ValueError, KeyError, TypeError):
        return detail[:300]


def _retry_after(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Seconds to wait after a 429: Retry-After header, else 'try again in Xs', else backoff."""
    header = exc.headers.get("retry-after") if exc.headers else None
    if header:
        try:
            return min(float(header) + 0.5, 60.0)
        except ValueError:
            pass
    return min(2.0 * (2 ** attempt), 30.0)


def estimate_cost(prompt_tokens: int, completion_tokens: int, settings: Settings | None = None) -> float:
    s = settings or get_settings()
    return prompt_tokens / 1e6 * s.price_in_per_m + completion_tokens / 1e6 * s.price_out_per_m
