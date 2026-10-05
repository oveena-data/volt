"""LLM provider adapter.

A single `chat()` entry point that returns a reply plus a token count,
so the game engine can meter usage regardless of which backend is in use.

Two backends:

* ``openai_compatible`` -- POSTs to any ``/chat/completions`` endpoint that
  speaks the OpenAI schema. That covers free/open options the operator can
  point at: a local Ollama server, Groq's free tier, OpenRouter free models,
  vLLM, llama.cpp's server, etc. Real token usage is read from the response
  when present, else estimated.

* ``mock`` -- a deterministic, offline "model" whose only purpose is to make
  every level fully playable and testable with no network and no API key.
  It is NOT a real model: each level ships a small hand-written policy
  (see ``app.levels``) describing, in code, the conditions under which the
  mock "model" complies. This lets the whole CTF run in CI and in a browser
  demo. Swap ``VOLT_PROVIDER=openai_compatible`` to play against a genuine
  model, where the *same* level system prompts become real jailbreak targets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx

from ..config import settings


@dataclass
class ChatResult:
    text: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


Message = dict  # {"role": "system"|"user"|"assistant", "content": str}


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) used when a backend omits usage."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _estimate_messages(messages: list[Message]) -> int:
    return sum(estimate_tokens(m.get("content", "")) + 4 for m in messages)


class MockPolicyError(RuntimeError):
    """Raised when the mock provider is asked for a level with no policy bound."""


# A module-level registry the mock provider consults. Level modules register a
# callable (messages -> reply_text). Keeps the provider ignorant of level logic.
_mock_policies: dict[str, "MockPolicy"] = {}


class MockPolicy:
    """Interface for a level's deterministic mock behaviour."""

    def respond(self, messages: list[Message]) -> str:  # pragma: no cover - interface
        raise NotImplementedError


def register_mock_policy(level_id: str, policy: MockPolicy) -> None:
    _mock_policies[level_id] = policy


async def chat(messages: list[Message], *, level_id: str | None = None) -> ChatResult:
    """Send a chat completion. ``level_id`` selects the mock policy when the
    mock provider is active; it is ignored by real backends."""
    if settings.provider == "mock":
        return _mock_chat(messages, level_id)
    return await _openai_compatible_chat(messages)


def _mock_chat(messages: list[Message], level_id: str | None) -> ChatResult:
    if not level_id or level_id not in _mock_policies:
        # Generic fallback so the system never hard-fails.
        reply = (
            "[mock model] No level policy is bound to this conversation, so I "
            "can only echo that I received your message."
        )
    else:
        reply = _mock_policies[level_id].respond(messages)
    return ChatResult(
        text=reply,
        prompt_tokens=_estimate_messages(messages),
        completion_tokens=estimate_tokens(reply),
    )


async def _openai_compatible_chat(messages: list[Message]) -> ChatResult:
    url = settings.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    text = data["choices"][0]["message"]["content"] or ""
    usage = data.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens") or _estimate_messages(messages)
    completion_tokens = usage.get("completion_tokens") or estimate_tokens(text)
    return ChatResult(
        text=text,
        prompt_tokens=int(prompt_tokens),
        completion_tokens=int(completion_tokens),
    )
