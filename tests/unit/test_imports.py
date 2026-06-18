"""Тесты проверки импортов и базовой структуры."""

import factorydaemon
from factorydaemon import cli, config


def test_version_is_defined() -> None:
    """Версия пакета определена."""
    assert factorydaemon.__version__ == "0.1.0"


def test_config_imports() -> None:
    """Модуль настроек импортируется без ошибок."""
    assert config.settings.project_name == "FactoryDaemon"


def test_cli_app_exists() -> None:
    """CLI приложение создано."""
    assert cli.app is not None
