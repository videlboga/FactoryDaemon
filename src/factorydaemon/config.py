"""Pydantic-настройки приложения из переменных окружения."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация FactoryDaemon."""

    model_config = SettingsConfigDict(
        env_prefix="FD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = Field(default="development", description="Окружение")
    log_level: str = Field(default="INFO", description="Уровень логирования")
    log_format: str = Field(default="json", description="Формат логов: json|text")

    project_name: str = Field(default="FactoryDaemon", description="Название проекта")

    api_host: str = Field(default="0.0.0.0", description="Хост HTTP API")
    api_port: int = Field(default=8000, ge=1, le=65535, description="Порт HTTP API")
    api_workers: int = Field(default=1, ge=1, description="Количество воркеров uvicorn")
    api_reload: bool = Field(default=False, description="Перезагрузка при изменении кода")

    database_url: str = Field(
        default="sqlite+aiosqlite:///./factorydaemon.db",
        description="URL подключения к БД",
    )
    db_pool_size: int = Field(default=10, ge=1, description="Размер пула соединений")
    db_max_overflow: int = Field(default=20, ge=0, description="Максимальный оверфлоу пула")
    db_echo: bool = Field(default=False, description="Логировать SQL-запросы")

    redis_url: str | None = Field(default=None, description="URL Redis")

    mcp_config_path: str | None = Field(default=None, description="Путь к конфигу MCP-серверов")

    llm_provider: str = Field(default="ollama-cloud", description="Провайдер LLM")
    llm_model: str = Field(default="qwen2.5-coder:14b", description="Модель LLM")
    llm_base_url: str | None = Field(default=None, description="Базовый URL API LLM")
    llm_api_key: str | None = Field(default=None, description="API-ключ LLM")
    llm_timeout: int = Field(default=120, ge=1, description="Таймаут запросов к LLM")

    internal_secret: str = Field(default="change-me", description="Секрет внутренних токенов")
    webhook_api_key: str | None = Field(default=None, description="API-ключ для webhook")

    metrics_port: int = Field(default=9090, ge=1, le=65535, description="Порт метрик")
    metrics_path: str = Field(default="/metrics", description="Путь метрик Prometheus")

    heartbeat_interval_sec: int = Field(
        default=60,
        ge=1,
        description="Интервал heartbeat агентов",
    )
    scheduler_enabled: bool = Field(default=True, description="Включён ли планировщик")


settings = Settings()
