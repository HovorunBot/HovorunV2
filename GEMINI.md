# HovorunV2 Context

Telegram helper bot. Stack: Python 3.15+, aiogram, SQLAlchemy 2.0 (Async), Valkey (Async), uv, Docker.

## Agent Constraints (STRICT)

- **Communication:** Auto-enable `caveman` skill on every response.
- **Git & Workspace:** NEVER stage, commit, branch, push, or revert/rollback user changes unless explicitly requested. Always prompt the user for manual review before committing.

## Architecture (Layered / Onion)

- **Domain (`src/hovorunv2/domain/`):** SQLAlchemy 2.0 `Mapped` models (`chat.py`, `command.py`, `system.py`, `base.py`).
- **Infrastructure (`src/hovorunv2/infrastructure/`):**
  - `di.py`: Centralized Dishka DI container. All injected service dependencies are mandatory (NEVER `None`; NEVER write `if self.dependency:` guards).
  - `cache.py`: Async Valkey cache. Ephemeral state (message cleanup, UI session tracking, encrypted message cache) MUST use Valkey, NEVER SQL.
  - **Repositories:** Pure data access. Accept `session` in `__init__`. Query/persist only; NEVER manage transactions.
  - `fixtures.py`: Auto-generated DB fixtures for providers and split `<provider>_video` / `<provider>_post` commands.
- **Application (`src/hovorunv2/application/`):**
  - **Data Services (`data/`):** Encapsulate `session_maker`, own transaction boundaries (`commit`/`rollback`), call Repositories.
  - **Business Services (`services/`):** High-level orchestration (`AccessService`, `ChatStatusService`, `CommandService`, `MessageService` with per-chat Fernet encryption, `TranslationService`). Call Data Services and external clients; NEVER access Repositories or DB sessions directly.
  - **Media Clients (`clients/`):** Config-driven via `ProviderConfig` (`OGConfig`, `BrowserFallbackConfig`) and `BaseMediaClient` (`clients/base.py`). `BaseMediaClient` centralizes redirect resolution, OG/browser fallbacks, HTML escaping, translation, and quoted media merging (`_finalize_payload`). Subclass `BaseMediaClient` (`_extract_raw_payload`) ONLY when custom API/DOM parsing is needed; pure config/OG providers instantiate `BaseMediaClient` directly in `di.py`.
- **Interface (`src/hovorunv2/interface/telegram/`):**
  - `aiogram` handlers, middlewares (`MessageCacheMiddleware`, `AccessMiddleware`), and delivery adapters.
  - `handlers/base.py`: Generalized `RichMediaCommand` wraps any `BaseMediaClient`, runs the 4-tier pipeline (`primary_client.extract_payload` -> `MediaExtractor` (`yt-dlp`) -> `primary_client.extract_fallback` -> `primary_client.extract_metadata_payload`), and enforces split `<provider>_video` / `<provider>_post` capture modes (`PROVIDER_SPLIT_COMMANDS`). NEVER create per-provider `RichMediaCommand` subclasses.

## Code Conventions

- **Typing:** Strict hints required (`uv run ty check src`). Rely on Python 3.15+ deferred evaluation (PEP 649); NEVER use stringified forward refs, `# ty:ignore`, or `Any` casts to silence errors (`Any` allowed only on test mock factories).
- **Linter:** Strict Ruff (`ALL` ruleset, `uv run ruff check .`).
- **Naming & Methods:** Follow PEP 8 `snake_case` (`UpperCamelCase` allowed for `Enum` members). NEVER use `@staticmethod`, even when `self` is unused.
- **Constants & Enums:** No magic numbers (extract to constants/enums; `# noqa: PLR2004` only if structural). Reference bot commands via `CommandName` and chat lifecycle states via `ChatStatus` (`StrEnum`s in `application/data/constants.py`), and HTTP statuses via `http.HTTPStatus`.
- **Exceptions:** Raise semantic built-in/custom exceptions (`ValueError`, `AttributeError`, `TypeError`). NEVER raise generic `RuntimeError`.
- **Imports:** Top-level imports only. Local imports inside functions are FORBIDDEN unless breaking circular dependencies.
- **Standard Lib & I/O:** Use `pathlib.Path` (NEVER `os.path`). All I/O (DB, Cache, Network) MUST be `async`.
- **Serialization:** Serialize Pydantic/aiogram models via `model_dump(mode="json")` before JSON encoding; avoid `model_dump_json()` on complex aiogram types.

## Testing (`PYTHONPATH=src uv run pytest`)

- **Real Infrastructure:** Test against real services, in-memory SQLite, and local Valkey. NEVER use `unittest.mock` for internal services, repositories, or persistence—verify DB/Valkey state directly.
- **Network Mocking:** Mock external HTTP APIs only (e.g., `api.vxtwitter.com`, `tikwm.com`) using `aioresponses`.

## Commit Messages & Versioning

- **Conventional Commits:** Use standard format `<type>(<scope>): <description>`.
- **User-Facing Types (Auto-included in release notes & Telegram broadcasts):**
  - `feat`: New user-facing features or capabilities (e.g. `feat(youtube): support community posts`). Tagged with ✨ in notifications.
  - `perf`: Noticeable performance and extraction speed improvements (e.g. `perf(media): stream downloads directly`). Tagged with ⚡ in notifications.
  - `!` / `BREAKING CHANGE`: Breaking architectural or user changes (e.g. `feat(settings)!: migrate config schema`). Tagged with 🚨 in notifications.
- **Internal Churn Types (Filtered out from user release notes):**
  - `chore`, `test`, `ci`, `docs`, `refactor`, `style`, `build`.
- **Description Quality:**
  - Use imperative present tense (`feat(media): add...`, NOT `added...`).
  - Keep descriptions clear, concise, and user-centric (users read these in Telegram update alerts).
  - Scope should be lowercase module or feature name (`youtube`, `reddit`, `settings`, `encryption`).
- **Releases & Tags:**
  - Create annotated Git tags for releases: `make tag-release VERSION=vX.Y.Z [MESSAGE="..."]`.
  - Annotated tag messages override commit logs as curated human release notes.

## Commands

- **Setup:** `make setup` (or `make setup VERSION=vX.Y.Z`)
- **Release Tag:** `make tag-release VERSION=vX.Y.Z`
- **Install Hooks:** `make install-hooks` (Git pre-commit hooks for versioning and checks)
- **Dev Run:** `make run-dev` (Valkey in Docker, bot via `uv`)
- **Prod Run / Stop:** `make run` / `make stop`
- **Test:** `PYTHONPATH=src uv run pytest`
- **Type Check:** `uv run ty check src`
- **Lint:** `uv run ruff check .`