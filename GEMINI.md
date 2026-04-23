# HovorunV2 Project Context

HovorunV2 is a Telegram helper bot built with Python 3.14+, following **Onion Architecture** and **SOLID** principles.

## Project Overview

- **Main Technologies:** Python 3.14, [aiogram](https://github.com/aiogram/aiogram), [SQLAlchemy 2.0](https://www.sqlalchemy.org/) (Async), [Valkey](https://github.com/valkey-io/valkey) (Async Cache), [uv](https://github.com/astral-sh/uv), [Docker](https://www.docker.com/).
- **Architecture (Onion):**
  - **Domain:** Pure database models using modern SQLAlchemy `Mapped` types.
  - **Application:** Service layer for business logic (e.g., `WhitelistService`, `TranslationService`, `MessageService`).
  - **Infrastructure:** Async Redis-compatible caching (Valkey), Database repositories, and configuration.
  - **Interface:** Delivery adapters (Telegram Bot handlers via `aiogram`).

## Dependency Management

The project uses a centralized `Container` (`infrastructure/container.py`) for global service management. All caching operations are **asynchronous** and use **JSON** serialization for security.

## Building and Running

Automated via `Makefile`.

### Commands
- **Setup:** `make setup` - Prepares `.env`, data directory, and builds Docker images.
- **Run (Prod):** `make run` - Starts the bot and Valkey in Docker.
- **Stop:** `make stop` - Stops all Docker services.
- **Run (Dev):** `make run-dev` - Starts Valkey in Docker and runs Bot locally via `uv`.
- **Test:** `PYTHONPATH=src uv run pytest` - Runs the test suite (requires Valkey).
- **Type Check:** `uv run ty check src` - Static type checking.

## Development Conventions

### Code Style & Quality
- **Linter:** Strict [Ruff](https://github.com/astral-sh/ruff) configuration (`ALL`).
- **Type Hints:** Mandatory. Checked with `ty`.
- **Async:** Mandatory for all I/O operations (Database, Cache, Network).
- **JSON Serialization:** Use `model_dump_json()` for Pydantic/Aiogram objects to handle non-serializable types correctly.
- **HTTP Status Codes:** Use `HTTPStatus` enum.
- **Magic Numbers:** Define as constants/enums.

## Key Files
- `src/hovorunv2/__main__.py`: Application entry point.
- `src/hovorunv2/infrastructure/container.py`: Service container.
- `src/hovorunv2/infrastructure/cache.py`: Async Valkey cache service.
- `src/hovorunv2/application/services/`: Business logic services.
- `src/hovorunv2/interface/telegram/`: Aiogram implementation.
- `docker-compose.yml`: Unified Docker configuration.
