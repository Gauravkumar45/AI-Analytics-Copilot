"""Minimal OpenAI-compatible chat client with tool calling and usage tracking.

Uses only the standard library, so it works against Groq, OpenAI, Gemini's
OpenAI endpoint or Ollama without an SDK dependency.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.settings import Settings, get_settings


class LLMError(RuntimeError):
    """The model could not be reached or returned an unusable response."""


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
             temperature: float = 0.0, max_tokens: int = 700) -> LLMResponse: ...


class OpenAICompatibleClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.llm_model

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.0, max_tokens: int = 700) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        request = urllib.request.Request(
            self.settings.llm_base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.settings.llm_timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:300]
            if exc.code == 401:
                raise LLMError("LLM API key is missing or invalid (set GROQ_API_KEY or LLM_API_KEY)") from exc
            if exc.code == 429:
                raise LLMError("LLM rate limit reached; try again in a minute") from exc
            raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LLMError(f"Could not reach the LLM at {self.settings.llm_base_url}: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

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


def estimate_cost(prompt_tokens: int, completion_tokens: int, settings: Settings | None = None) -> float:
    s = settings or get_settings()
    return prompt_tokens / 1e6 * s.price_in_per_m + completion_tokens / 1e6 * s.price_out_per_m
