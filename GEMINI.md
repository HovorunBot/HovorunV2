# HovorunV2 Project Context

HovorunV2 is a Telegram helper bot built with Python 3.14+, following **Onion Architecture** and **SOLID** principles.

## Project Overview

- **Main Technologies:** Python
  3.14, [aiogram](https://github.com/aiogram/aiogram), [SQLAlchemy 2.0](https://www.sqlalchemy.org/) (
  Async), [Valkey](https://github.com/valkey-io/valkey) (Async
  Cache), [uv](https://github.com/astral-sh/uv), [Docker](https://www.docker.com/).
- **Architecture (Onion):**
    - **Domain:** Pure database models using modern SQLAlchemy `Mapped` types.
    - **Infrastructure:** Async Redis-compatible caching (Valkey), configuration, and Database repositories.
        - **Repositories:** Pure data access. They know how to query/persist objects but *never* manage transactions or
          sessions. They accept a `session` in their constructor.
    - **Application:** Split into two layers:
        - **Data Services:** Intermediary layer (e.g., `ChatDataService`, `CommandDataService`). They encapsulate `session_maker`, manage
          transaction boundaries (`commit`/`rollback`), and use Repositories to perform operations. They provide
          high-level data primitives to business services.
        - **Business Services:** High-level logic (e.g., `WhitelistService`, `LanguageService`, `CommandService`). They focus on "what"
          should happen. They use Data Services to interact with the database and never touch repositories or sessions
          directly.
    - **Interface:** Delivery adapters (Telegram Bot handlers via `aiogram`).

## Dependency Management

The project uses a centralized `Container` (`infrastructure/container.py`) for global service management. All caching
operations are **asynchronous** and use **JSON** serialization for security.

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

- **Magic Numbers:** FORBIDDEN. All numeric literals (except `0`, `1`, `-1` in obvious loop/index contexts) must be defined as named constants or enums. This includes thresholds, timeouts, limits, and array indices. Use `noqa: PLR2004` ONLY if the value is truly structural and non-configurable, but prefer constants.
- **Errors:** ALWAYS use semantic exceptions (e.g., `ValueError`, `AttributeError`, `TypeError`) or custom domain
  exceptions. NEVER raise generic `RuntimeError`.
- **Linter:** Strict [Ruff](https://github.com/astral-sh/ruff) configuration (`ALL`).
- **Type Hints:** Mandatory. Checked with `ty`. As we use Python 3.14+, deferred evaluation of type annotations is the
  default; do NOT use strings (`"Service"`) for forward references or circular dependencies.
- **Async:** Mandatory for all I/O operations (Database, Cache, Network).
- **JSON Serialization:** Use `model_dump(mode="json")` for Pydantic/Aiogram objects before stringifying to handle
  non-serializable types like `Default` correctly. Avoid direct `model_dump_json()` on complex Aiogram types.
- **HTTP Status Codes:** Use `HTTPStatus` enum.
- **Magic Numbers:** Define as constants/enums.

### Testing Requirements

- **No Mocks Policy:** Internal code must be tested without mocks. ALWAYS use real services, real objects, real
  databases (SQLite memory for tests), and real Valkey instances.
- **External APIs:** Only external network requests (e.g., to `api.vxtwitter.com`, `tikwm.com`) may be mocked. Use
  `aioresponses` or similar to mock at the HTTP layer while keeping service logic real.
- **Persistence:** Avoid `unittest.mock` where real implementations can be used. Verify state in Database and Cache
  directly.
- **Type Integrity:** NEVER resolve type-checker (`ty`) errors by using `# ty:ignore` or `Any` casts to suppress
  warnings. Resolve the root cause via proper type hints or improved mock structures. Use `Any` return type for mock
  factories if needed to avoid casting in every assertion.
- **Persistence of User Changes:** NEVER rollback or revert code changes provided by the user unless explicitly
  instructed. This is a critical rule to ensure progress is not lost.
- **Mandatory Dependencies:** Services must require their dependencies in `__init__`. Injected dependencies from the
  `Container` MUST NOT be `None`. Avoid `Optional` or `None` defaults for core service dependencies. No `if dependency:`
  nonsense.

## Key Files

- `src/hovorunv2/domain/chat.py`: Chat model and M2M associations.
- `src/hovorunv2/domain/command.py`: Command model.
- `src/hovorunv2/infrastructure/container.py`: Service container.
- `src/hovorunv2/infrastructure/fixtures.py`: Database fixtures.
- `src/hovorunv2/infrastructure/cache.py`: Async Valkey cache service.
- `src/hovorunv2/application/services/`: Business logic services.
- `src/hovorunv2/interface/telegram/`: Aiogram implementation.
- `docker-compose.yml`: Unified Docker configuration.
