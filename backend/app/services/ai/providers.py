"""Provider-independent AI layer (OpenAI, Anthropic, Google Gemini) over plain HTTPS.

Providers return raw text; ``call_structured`` parses it as JSON and validates it with a Pydantic model, retrying
once with the validation error when the model returns malformed output. API keys are never logged.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
PROVIDERS = ("openai", "anthropic", "gemini")


class AIError(RuntimeError):
    """A provider failure that callers handle gracefully (analysis falls back to rules only)."""


@dataclass
class AIUsage:
    input_tokens: int = 0
    output_tokens: int = 0


class AIProvider:
    name = "base"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.usage = AIUsage()

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def complete(self, system: str, user: str, max_tokens: int) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def _post(self, url: str, headers: dict[str, str], body: dict) -> dict:
        timeout = get_settings().ai_timeout_seconds
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=httpx.Timeout(timeout, connect=10))
        except httpx.TimeoutException as exc:
            raise AIError(f"{self.name} request timed out after {timeout}s") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"{self.name} request failed: {type(exc).__name__}") from exc
        if resp.status_code in (401, 403):
            raise AIError(f"{self.name} rejected the API key (HTTP {resp.status_code})")
        if resp.status_code == 429:
            raise AIError(f"{self.name} rate limit or quota exceeded (HTTP 429)")
        if resp.status_code >= 400:
            raise AIError(f"{self.name} returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise AIError(f"{self.name} returned a non-JSON response") from exc


class OpenAIProvider(AIProvider):
    name = "openai"

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        data = self._post(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {self.api_key}"},
            {"model": self.model, "max_tokens": max_tokens, "temperature": 0,
             "response_format": {"type": "json_object"},
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        try:
            usage = data.get("usage") or {}
            self.usage.input_tokens += int(usage.get("prompt_tokens", 0))
            self.usage.output_tokens += int(usage.get("completion_tokens", 0))
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("openai returned an unexpected response shape") from exc


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        data = self._post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            {"model": self.model, "max_tokens": max_tokens, "system": system,
             "messages": [{"role": "user", "content": user}]},
        )
        try:
            usage = data.get("usage") or {}
            self.usage.input_tokens += int(usage.get("input_tokens", 0))
            self.usage.output_tokens += int(usage.get("output_tokens", 0))
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise AIError("anthropic returned an unexpected response shape") from exc


class GeminiProvider(AIProvider):
    name = "gemini"

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        data = self._post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            {"x-goog-api-key": self.api_key},
            {"systemInstruction": {"parts": [{"text": system}]},
             "contents": [{"role": "user", "parts": [{"text": user}]}],
             "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens,
                                  "responseMimeType": "application/json"}},
        )
        try:
            meta = data.get("usageMetadata") or {}
            self.usage.input_tokens += int(meta.get("promptTokenCount", 0))
            self.usage.output_tokens += int(meta.get("candidatesTokenCount", 0))
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("gemini returned an unexpected or blocked response") from exc


_CLASSES: dict[str, type[AIProvider]] = {"openai": OpenAIProvider, "anthropic": AnthropicProvider, "gemini": GeminiProvider}


def make_provider(name: str, api_key: str, model: str) -> AIProvider:
    if name not in _CLASSES:
        raise AIError(f"Unknown AI provider '{name}'")
    if not api_key:
        raise AIError(f"No API key configured for {name}")
    return _CLASSES[name](api_key, model)


def _extract_json(text: str) -> object:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


def call_structured[T: BaseModel](provider: AIProvider, system: str, user: str, model_cls: type[T], max_tokens: int | None = None) -> T:
    s = get_settings()
    max_tokens = max_tokens or s.ai_max_output_tokens
    prompt = user
    last_error = ""
    for attempt in range(s.ai_max_retries + 1):
        raw = provider.complete(system, prompt, max_tokens)
        try:
            return model_cls.model_validate(_extract_json(raw))
        except (ValueError, ValidationError) as exc:
            last_error = type(exc).__name__
            log.warning("AI response failed validation (attempt %s, provider %s)", attempt + 1, provider.name)
            detail = str(exc)[:800] if isinstance(exc, ValidationError) else "Response was not valid JSON."
            prompt = (user + "\n\nYour previous response was invalid: " + detail +
                      "\nReturn ONLY one JSON object that matches the required schema.")
    raise AIError(f"The AI response could not be validated ({last_error})")
