"""Tests for services/llm.py."""

from __future__ import annotations

import pytest

from factorydaemon.services.llm import LLMError, chat


@pytest.mark.asyncio
async def test_chat_raises_without_token(monkeypatch):
    monkeypatch.setattr("factorydaemon.services.llm.settings.llm_api_key", None)
    monkeypatch.setattr(
        "factorydaemon.services.llm.settings.llm_base_url", "http://localhost:9999/v1"
    )
    with pytest.raises(LLMError):
        await chat([{"role": "user", "content": "test"}], timeout=1)
