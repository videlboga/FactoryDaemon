"""Tests for bot/telegram.py imports."""

from __future__ import annotations


def test_telegram_dispatcher_imports():
    from factorydaemon.bot.telegram import dp

    assert dp is not None


def test_get_bot_raises_without_token(monkeypatch):
    import pytest

    from factorydaemon.bot.telegram import get_bot

    monkeypatch.setattr("factorydaemon.bot.telegram.settings.telegram_bot_token", None)
    with pytest.raises(RuntimeError):
        get_bot()
