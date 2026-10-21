# HovorunV2 Context

Telegram helper bot. Stack: Python 3.14+, aiogram, SQLAlchemy 2.0 (Async), Valkey (Async), uv, Docker.

## Agent Constraints (STRICT)

- **Communication:** Auto-enable `caveman` skill on every response.
- **Git & Workspace:** NEVER stage, commit, branch, push, or revert/rollback user changes unless explicitly requested. Always prompt the user for manual review before committing.

## Architecture (Layered / Onion)

- **Domain (`src/hovorunv2/domain/`):** SQLAlchemy 2.0 `Mapped` models (`chat.py`, `command.py`).
- **Infrastructure (`src/hovorunv2/infrastructure/`):**
  - `container.py`: Centralized DI container. All injected service dependencies are mandatory (NEVER `None`; NEVER write `if self.dependency:` guards).
  - `cache.py`: Async Valkey cache. Ephemeral state (message cleanup, UI session tracking) MUST use Valkey Sorted Sets, NEVER SQL.
  - **Repositories:** Pure data access. Accept `session` in `__init__`. Query/persist only; NEVER manage transactions.
  - `fixtures.py`: DB fixtures.
- **Application (`src/hovorunv2/application/`):**
  - **Data Services:** Encapsulate `session_maker`, own transaction boundaries (`commit`/`rollback`), call Repositories.
  - **Business Services (`services/`):** High-level orchestration. Call Data Services and external clients; NEVER access Repositories or DB sessions directly.
- **Interface (`src/hovorunv2/interface/telegram/`):** `aiogram` handlers and delivery adapters.

## Code Conventions

- **Typing:** Strict hints required (`uv run ty check src`). Rely on Python 3.14 deferred evaluation (PEP 649); NEVER use stringified forward refs, `# ty:ignore`, or `Any` casts to silence errors (`Any` allowed only on test mock factories).
- **Linter:** Strict Ruff (`ALL` ruleset).
- **Naming & Methods:** Follow PEP 8 `snake_case` (`UpperCamelCase` allowed for `Enum` members). NEVER use `@staticmethod`, even when `self` is unused.
- **Constants & Enums:** No magic numbers (extract to constants/enums; `# noqa: PLR2004` only if structural). Reference bot commands via `CommandName` (`StrEnum` in `application/data/constants.py`) and HTTP statuses via `http.HTTPStatus`.
- **Exceptions:** Raise semantic built-in/custom exceptions (`ValueError`, `AttributeError`, `TypeError`). NEVER raise generic `RuntimeError`.
- **Imports:** Top-level imports only. Local imports inside functions are FORBIDDEN unless breaking circular dependencies.
- **Standard Lib & I/O:** Use `pathlib.Path` (NEVER `os.path`). All I/O (DB, Cache, Network) MUST be `async`.
- **Serialization:** Serialize Pydantic/aiogram models via `model_dump(mode="json")` before JSON encoding; avoid `model_dump_json()` on complex aiogram types.

## Testing (`PYTHONPATH=src uv run pytest`)

- **Real Infrastructure:** Test against real services, in-memory SQLite, and local Valkey. NEVER use `unittest.mock` for internal services, repositories, or persistence—verify DB/Valkey state directly.
- **Network Mocking:** Mock external HTTP APIs only (e.g., `api.vxtwitter.com`, `tikwm.com`) using `aioresponses`.

## Commands

- **Setup:** `make setup`
- **Dev Run:** `make run-dev` (Valkey in Docker, bot via `uv`)
- **Prod Run / Stop:** `make run` / `make stop`
- **Test:** `PYTHONPATH=src uv run pytest`
- **Type Check:** `uv run ty check src`