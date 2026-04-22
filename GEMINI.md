# HovorunV2 Project Context

HovorunV2 is a Telegram helper bot built with Python 3.14+, following **Onion Architecture** and **SOLID** principles.

## Project Overview

- **Main Technologies:** Python 3.14, [aiogram](https://github.com/aiogram/aiogram), [SQLAlchemy](https://www.sqlalchemy.org/) (Async), [Injector](https://github.com/alecthomas/injector), [Pydantic Settings](https://github.com/pydantic/pydantic-settings), [uv](https://github.com/astral-sh/uv), [diskcache-rs](https://github.com/grantjenks/python-diskcache).
- **Architecture (Onion):**
  - **Domain:** Pure entities (`domain/models/`) and repository interfaces (`domain/repositories/`).
  - **Application:** Use cases and services (`application/services/`). Orchestrates domain logic.
  - **Infrastructure:** External implementations (`infrastructure/database/`, `infrastructure/cache/`, `infrastructure/di/`).
  - **Interface:** Delivery adapters (`interface/telegram/`). handlers and commands.

## Building and Running

This project uses a `Makefile` to automate most tasks.

### Commands
- **Setup:** `make setup` - Installs `uv`, Python 3.14, project dependencies, and initializes the `.env` file.
- **Run:** `make run` - Starts the bot using `uv run hovorunv2`.
- **Stop:** `make stop` - Stops the daemonized application.
- **Test:** `PYTHONPATH=src uv run pytest` - Runs the test suite.

## Development Conventions

### Adding New Features
1. **Domain:** Define models and repository protocols.
2. **Infrastructure:** Implement the protocols (e.g., SQLAlchemy repositories).
3. **Application:** Create services for business logic.
4. **DI:** Wire new dependencies in `infrastructure/di/modules.py`.
5. **Interface:** Implement the delivery handler (e.g., Telegram command).

### Code Style & Quality
- **Linter:** Strict [Ruff](https://github.com/astral-sh/ruff) configuration (`ALL`). Target version `py314`.
- **Docstrings:** Google convention.
- **Type Hints:** Required for all function signatures.

## Key Files
- `src/hovorunv2/__main__.py`: Bootstrap, DI initialization, and app entry point.
- `src/hovorunv2/infrastructure/di/modules.py`: Dependency injection configuration.
- `src/hovorunv2/infrastructure/database/models/`: SQLAlchemy ORM models.
- `src/hovorunv2/application/services/`: Core business logic services.
- `src/hovorunv2/interface/telegram/`: Aiogram specific implementation.
