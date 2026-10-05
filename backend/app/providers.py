"""Inference provider layer.

One entry point, `generate()`, guarded by:
  * a shared concurrency semaphore (VOLT_INFERENCE_CONCURRENCY)
  * a bounded wait queue (VOLT_INFERENCE_QUEUE_MAX) -> 429 when saturated
  * a hard request timeout -> provider_timeout
Provider failures raise ProviderError; the game engine records them without
charging the player an attempt.

Backends:
  * openai_compatible — any /chat/completions endpoint (Ollama, vLLM,
    llama.cpp server, Groq, OpenRouter...). The production backend.
  * mock — ONLY available when VOLT_ENV is 'test' or 'development'. It exists
    for isolated engine tests; production startup refuses it outright, and
    there is no silent fallback anywhere.

Qwen3 note: thinking output is stripped defensively (<think>...</think>), and
VOLT_EXTRA_BODY can pass raw JSON (e.g. {"chat_template_kwargs":
{"enable_thinking": false}}) to pin non-thinking mode where the server
supports it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass

import httpx

from .config import settings

log = logging.getLogger("volt.provider")


class ProviderError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # provider_timeout | provider_error | provider_overloaded


class QueueFullError(ProviderError):
    def __init__(self):
        super().__init__("queue_full", "inference queue is full; retry shortly")


@dataclass
class GenResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    model: str


# Qwen3-style reasoning: strip every closed <think> block wherever it sits,
# and an unterminated trailing one (max_tokens can cut generation off inside
# the block, which previously left raw reasoning in the player-visible reply
# and hid genuinely extracted flags from leak detection).
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_OPEN_THINK_RE = re.compile(r"<think>(?:(?!</think>).)*\Z", re.DOTALL)

_semaphore: asyncio.Semaphore | None = None
_waiting = 0
_client: httpx.AsyncClient | None = None

# test hook: tests may install an async callable (messages, params) -> str
_mock_responder = None


def set_mock_responder(fn) -> None:
    global _mock_responder
    _mock_responder = fn


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.inference_concurrency)
    return _semaphore


def http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(settings.request_timeout,
                                                           connect=10.0))
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def queue_depth() -> int:
    return _waiting


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


async def generate(messages: list[dict], *, temperature: float | None = None,
                   max_tokens: int | None = None) -> GenResult:
    global _waiting
    if _waiting >= settings.inference_queue_max:
        raise QueueFullError()
    _waiting += 1
    try:
        async with _sem():
            return await _dispatch(messages, temperature=temperature,
                                   max_tokens=max_tokens)
    finally:
        _waiting -= 1


async def _dispatch(messages, *, temperature, max_tokens) -> GenResult:
    start = time.monotonic()
    if settings.provider == "mock":
        if settings.env == "production":
            # Defence in depth; startup validation already refuses this.
            raise ProviderError("provider_error",
                                "mock provider is not permitted in production")
        if _mock_responder is None:
            raise ProviderError("provider_error", "no mock responder installed")
        text = await _mock_responder(messages)
        return GenResult(
            text=text,
            prompt_tokens=sum(estimate_tokens(m.get("content", "")) for m in messages),
            completion_tokens=estimate_tokens(text),
            latency_ms=int((time.monotonic() - start) * 1000),
            model="mock",
        )
    return await _openai_compatible(messages, temperature=temperature,
                                    max_tokens=max_tokens, start=start)


def _extra_body() -> dict:
    raw = os.environ.get("VOLT_EXTRA_BODY", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("VOLT_EXTRA_BODY is not valid JSON; ignoring")
        return {}


async def _openai_compatible(messages, *, temperature, max_tokens, start) -> GenResult:
    url = settings.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": settings.temperature if temperature is None else temperature,
        "top_p": settings.top_p,
        "max_tokens": settings.max_tokens if max_tokens is None else max_tokens,
        "stream": False,
        **_extra_body(),
    }
    try:
        resp = await http_client().post(url, headers=headers, json=payload)
    except httpx.TimeoutException:
        raise ProviderError("provider_timeout",
                            f"inference timed out after {settings.request_timeout}s")
    except httpx.HTTPError as e:
        raise ProviderError("provider_error", f"inference transport error: {type(e).__name__}")

    if resp.status_code == 429:
        raise ProviderError("provider_overloaded", "inference endpoint rate-limited us")
    if resp.status_code >= 400:
        log.error("provider HTTP %s: %s", resp.status_code, resp.text[:500])
        raise ProviderError("provider_error", f"inference endpoint returned HTTP {resp.status_code}")

    try:
        data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, ValueError, TypeError):
        raise ProviderError("provider_error", "inference endpoint returned a malformed response")

    text = _THINK_RE.sub("", text)
    text = _OPEN_THINK_RE.sub("", text).strip()
    usage = data.get("usage") or {}
    return GenResult(
        text=text,
        prompt_tokens=int(usage.get("prompt_tokens")
                          or sum(estimate_tokens(m.get("content", "")) for m in messages)),
        completion_tokens=int(usage.get("completion_tokens") or estimate_tokens(text)),
        latency_ms=int((time.monotonic() - start) * 1000),
        model=str(data.get("model") or settings.model),
    )


async def probe() -> dict:
    """Readiness probe of the configured inference endpoint (GET /models)."""
    if settings.provider == "mock":
        return {"ok": settings.env != "production", "provider": "mock"}
    url = settings.base_url.rstrip("/") + "/models"
    headers = {}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    try:
        resp = await http_client().get(url, headers=headers)
        return {"ok": resp.status_code < 500, "provider": "openai_compatible",
                "status": resp.status_code}
    except httpx.HTTPError as e:
        return {"ok": False, "provider": "openai_compatible", "error": type(e).__name__}
