# FactoryDaemon

Агентная система для управления производством.

## Структура проекта

```
.
├── src/factorydaemon/          # Исходный код пакета
│   ├── __init__.py             # Версия и публичный API
│   ├── adapters/               # Адаптеры внешних систем (LLM, MCP, БД)
│   ├── agents/                 # Определения агентов и ролей
│   ├── api/                    # HTTP API (FastAPI)
│   ├── cli.py                  # Точка входа для CLI
│   ├── config.py               # Pydantic-настройки из env
│   ├── core/                   # Ядро: доменные модели, workflow, планировщик
│   ├── db/                     # SQLAlchemy модели, миграции, репозитории
│   ├── observability/          # Логирование, метрики, трассировка
│   └── services/               # Сервисы бизнес-логики
├── tests/                      # Тесты
│   ├── conftest.py
│   ├── unit/
│   └── integration/
├── alembic/                    # Миграции Alembic
├── docs/                       # Документация
├── scripts/                    # Вспомогательные скрипты
├── .env.example                # Пример переменных окружения
├── pyproject.toml              # Зависимости и инструменты
└── README.md                   # Этот файл
```

## Быстрый старт

```bash
# 1. Скопируйте пример конфигурации
cp .env.example .env

# 2. Создайте виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate

# 3. Установите пакет в режиме разработки
pip install -e ".[dev]"

# 4. Проверьте импорты
python -c "import factorydaemon; print(factorydaemon.__version__)"

# 5. Запустите CLI
factorydaemon --help
```

## Разработка

```bash
# Линтер и форматтер
ruff check src tests
ruff format src tests

# Статический анализ
mypy src

# Тесты
pytest
```

## Лицензия

MIT
