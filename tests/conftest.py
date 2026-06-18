"""Общие фикстуры для тестов."""

import pytest


@pytest.fixture
def sample_fixture() -> str:
    """Пример фикстуры для проверки импортов."""
    return "ok"
