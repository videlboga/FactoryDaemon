"""LLM client for FactoryDaemon.

Used for:
- natural-language clarifications with the user;
- validating plan results against business intent;
- explaining warnings in plain Russian.

The provider is configured through Settings (ollama-cloud, ollama-launch, openrouter, etc.).
"""

from __future__ import annotations

from typing import Any

import httpx

from factorydaemon.config import settings


class LLMError(Exception):
    """Raised when an LLM request fails."""


async def chat(
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 800,
    timeout: float | None = None,
) -> str:
    """Send a chat-completion request and return the assistant message content.

    Supports OpenAI-compatible endpoints (Ollama, OpenRouter, etc.).
    """
    base_url = settings.llm_base_url or _provider_base_url(settings.llm_provider)
    url = f"{base_url}/chat/completions"
    api_key = settings.llm_api_key

    headers: dict[str, str] = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout or settings.llm_timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"LLM HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
    except Exception as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc

    choices = data.get("choices", [])
    if not choices:
        raise LLMError("LLM returned no choices")

    return str(choices[0].get("message", {}).get("content", "")).strip()


def _provider_base_url(provider: str) -> str:
    """Default base URLs for supported providers."""
    urls = {
        "ollama-cloud": "https://ollama.com/v1",
        "ollama-launch": "http://127.0.0.1:11434/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    }
    return urls.get(provider, "https://ollama.com/v1")


async def explain_plan_issue(messages: list[dict[str, str]]) -> str:
    """Ask LLM to summarize planning problems for the user."""
    system = {
        "role": "system",
        "content": (
            "Ты — ассистент производственного планировщика FactoryDaemon. "
            "Объясняй проблемы кратко, по-русски, в 1-3 пункта. "
            "Предлагай конкретное действие пользователю."
        ),
    }
    return await chat([system, *messages], temperature=0.2, max_tokens=500)
