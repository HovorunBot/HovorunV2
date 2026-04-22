# HovorunV2 Project Context

HovorunV2 is a Telegram helper bot built with Python 3.14+, following **Onion Architecture** and **SOLID** principles.

## Project Overview

- **Main Technologies:** Python 3.14, [aiogram](https://github.com/aiogram/aiogram), [SQLAlchemy 2.0](https://www.sqlalchemy.org/) (Async), [Pydantic Settings](https://github.com/pydantic/pydantic-settings), [uv](https://github.com/astral-sh/uv), [diskcache-rs](https://github.com/grantjenks/python-diskcache).
- **Architecture (Onion):**
  - **Domain:** Pure database models using modern SQLAlchemy `Mapped` types.
  - **Application:** Service layer for business logic and orchestration (e.g., `WhitelistService`, `TranslationService`, `MessageService`).
  - **Infrastructure:** External implementations (Database repositories, disk caching, and configuration).
  - **Interface:** Delivery adapters (Telegram Bot handlers and commands).

## Dependency Management

The project uses a centralized `Container` (`infrastructure/container.py`) for global service management and ensuring proper initialization order of dependencies.

## Building and Running

This project uses a `Makefile` to automate tool installation and application lifecycle.

### Commands
- **Setup:** `make setup` - Installs `uv`, Python 3.14, project dependencies, and initializes the `.env` file.
- **Run:** `make run` - Starts the bot in the foreground.
- **Stop:** `make stop` - Stops the background bot process.
- **Test:** `PYTHONPATH=src uv run pytest` - Runs the test suite.

## Development Conventions

### Adding New Features
1. **Infrastructure:** Define SQLAlchemy models in `infrastructure/database/models/`.
2. **Infrastructure:** Implement repositories in `infrastructure/database/repositories/`.
3. **Application:** Create services in `application/services/`.
4. **Container:** Register and initialize new services in `infrastructure/container.py`.
5. **Interface:** Implement the command/handler in `interface/telegram/commands/`.
6. **Registration:** Import the new module in `interface/telegram/commands/__init__.py`.

### Code Style & Quality
- **Linter:** Strict [Ruff](https://github.com/astral-sh/ruff) configuration (`ALL`). Target version `py314`.
- **Docstrings:** Google convention.
- **Type Hints:** Mandatory for all function signatures.

## Key Files
- `src/hovorunv2/__main__.py`: Application entry point.
- `src/hovorunv2/infrastructure/container.py`: Service container.
- `src/hovorunv2/infrastructure/database/models/`: SQLAlchemy ORM models.
- `src/hovorunv2/application/services/`: Business logic services.
- `src/hovorunv2/interface/telegram/`: Aiogram implementation.
