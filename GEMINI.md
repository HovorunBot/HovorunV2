# HovorunV2 Project Context

Telegram helper bot, Python 3.14+, Onion Architecture, SOLID.

## Project Overview

- **Technologies:** Python 3.14, [aiogram](https://github.com/aiogram/aiogram), [SQLAlchemy 2.0](https://www.sqlalchemy.org/) (Async), [Valkey](https://github.com/valkey-io/valkey) (Async Cache), [uv](https://github.com/astral-sh/uv), [Docker](https://www.docker.com/).
- **Architecture (Onion):**
    - **Domain:** Pure DB models, modern SQLAlchemy `Mapped` types.
    - **Infrastructure:** Async Valkey cache, config, DB repos.
        - **Repositories:** Pure data access. Query/persist objects. No transaction management. Accept `session` in constructor.
    - **Application:** Two layers:
        - **Data Services:** Intermediary. Encapsulate `session_maker`, manage transactions (`commit`/`rollback`). Use Repos for ops. High-level data primitives.
        - **Business Services:** High-level logic. Focus on "what". Use Data Services, no direct Repo/session touch.
    - **Interface:** Delivery adapters (Telegram Bot handlers via `aiogram`).

## Dependency Management

Centralized `Container` (`infrastructure/container.py`) for global service management. Caching async, JSON serialization for security.

## Building and Running

Automated via `Makefile`.

### Commands

- **Setup:** `make setup` - Prepare `.env`, data dir, build Docker images.
- **Run (Prod):** `make run` - Start bot + Valkey in Docker.
- **Stop:** `make stop` - Stop Docker services.
- **Run (Dev):** `make run-dev` - Start Valkey in Docker, run Bot locally via `uv`.
- **Test:** `PYTHONPATH=src uv run pytest` - Run tests (require Valkey).
- **Type Check:** `uv run ty check src` - Static type check.

## Development Conventions

### Code Style & Quality

- **Magic Numbers:** FORBIDDEN. Define as named constants/enums. `noqa: PLR2004` only if structural.
- **Errors:** Use semantic exceptions (e.g. `ValueError`, `AttributeError`, `TypeError`). NEVER raise generic `RuntimeError`.
- **Linter:** Strict [Ruff](https://github.com/astral-sh/ruff) (`ALL`).
- **Type Hints:** Mandatory. Checked with `ty`. Python 3.14+ deferred eval default; no strings for forward refs.
- **Local Imports:** NEVER use local imports in functions UNLESS it is absolutely necessary (e.g. to break circular dependencies).
- **Path Handling:** MANDATORY `pathlib.Path`. Avoid `os.path`.
- **Async:** Mandatory for I/O (DB, Cache, Network).
- **JSON Serialization:** Use `model_dump(mode="json")` for Pydantic/Aiogram objects before stringify. Avoid `model_dump_json()` on complex Aiogram types.
- **HTTP Status Codes:** Use `HTTPStatus` enum.

### Testing Requirements

- **No Mocks Policy:** Internal code test without mocks. Use real services, DB (SQLite memory), Valkey.
- **External APIs:** Mock only external network (e.g. `api.vxtwitter.com`, `tikwm.com`). Use `aioresponses`.
- **Persistence:** Avoid `unittest.mock` if real impl available. Verify DB/Cache state directly.
- **Type Integrity:** NEVER use `# ty:ignore` or `Any` casts to suppress errors. Fix root cause via proper hints. `Any` return type for mock factories OK.
- **Persistence of User Changes:** NEVER rollback/revert user changes unless instructed. Critical.
- **Mandatory Dependencies:** Services require deps in `__init__`. Injected deps from `Container` MUST NOT be `None`. No `if dependency:` nonsense.

## Key Files

- `src/hovorunv2/domain/chat.py`: Chat model, M2M associations.
- `src/hovorunv2/domain/command.py`: Command model.
- `src/hovorunv2/infrastructure/container.py`: Service container.
- `src/hovorunv2/infrastructure/fixtures.py`: DB fixtures.
- `src/hovorunv2/infrastructure/cache.py`: Async Valkey cache.
- `src/hovorunv2/application/services/`: Business services.
- `src/hovorunv2/interface/telegram/`: Aiogram impl.
- `docker-compose.yml`: Unified Docker config.

## Agent Specific Rules

- **Communication:** Auto-enable `caveman` skill every response.
- **Git Operations:** NEVER stage, commit, create branches, or perform git mutations unless requested. OVERRIDES all other skills. Manual review before commit.
