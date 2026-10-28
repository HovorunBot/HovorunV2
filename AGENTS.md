# HovorunV2 Assistant Instructions

Telegram helper bot. Stack: Python 3.15+, aiogram, SQLAlchemy 2.0 (Async), PostgreSQL, Valkey (Async), Celery, uv, Docker. Layered/onion architecture: `domain` -> `application` -> `infrastructure` -> `interface` -> `bootstrap`.

## 1. Hard Constraints (STRICT)

1. **Privacy & Data Minimization:** Do not collect personal or private user information beyond the minimum required for Telegram identity, access control, and bot operation. Never request or store phone numbers, contact lists, or unrelated profile data. Store Telegram IDs and public usernames only where needed for identity and administration. Keep stored message contents encrypted; do not copy message contents into user tracking, analytics, audit records, or logs. Permission records and usage counters must contain only the operational metadata needed for their purpose. Any additional personal data collection requires explicit agreement before implementation.
2. **Secrets & Credential Protection:** Never print, copy, or summarize values from `.env`, credentials, tokens, or Telegram message contents into chat replies, prompts, reports, logs, or `ai-schemes/` files.
3. **Git & Workspace Authorization:** NEVER stage, commit, branch, push, rebase, or revert/rollback user changes unless explicitly requested. Always prompt the user for manual review before committing. Before every authorized branch commit, fetch the upstream and rebase the branch onto its upstream; never use a merge commit to combine agent work. Explicit user authorization to commit autonomously supersedes manual review. Worktrees and branches are permitted only when the user explicitly requests parallel work in that session; otherwise work sequentially in a single workspace.
4. **Local Planning Artifacts (`ai-schemes/`):** All architecture proposals, feature plans, refactoring plans, requirements documents, implementation blocks, decision logs, and implementation/review reports belong under `ai-schemes/`. `ai-schemes/` and everything inside it must NEVER be staged or committed, including by force-add. Keep the folder in `.gitignore`; do not add AI-ignore rules. Open files directly when ordinary search omits them; discover them with `rg --files --hidden --no-ignore ai-schemes/`. Local files may be absent in a fresh checkout: absence is not evidence of completed work, and agents must not reconstruct user decisions from guesses. A written specification or plan never counts as implemented functionality, and does not authorize application changes, Git commits, deployments, or production migrations.

## 2. Response Style

- Auto-enable `caveman` skill on every response if available in the tool; otherwise communicate tersely (answer first, fluff gone, every technical fact kept).
- Persist code, comments, reports, and documentation in normal technical prose.

## 3. Architecture Principles & Boundaries

- **Layer Boundaries:** Layers depend strictly inward: `bootstrap` -> `interface` -> `infrastructure` -> `application` -> `domain`. Enforced by `lint-imports`.
- **Ports & Adapters:** Application defines protocol contracts (`application/ports.py`); application imports only `domain`, stdlib, and third-party libraries—NEVER infrastructure or interface. Repositories accept session and query/persist; stores encapsulate session_maker and own transaction boundaries.
- **Dependency Injection:** Centralized Dishka DI container composition root (`bootstrap/container.py`). All injected service dependencies are mandatory (NEVER `None`; NEVER write `if self.dependency:` guards).
- **DI Typing Consistency:** Consumer parameter type hints MUST match provider definitions exactly (e.g. bare `async_sessionmaker` instead of generic `async_sessionmaker[AsyncSession]`) to avoid adaptix fallback matching and Python 3.15 deprecation warnings.
- **Reference Documentation:** See [architecture documentation](docs/architecture.md) for full layer responsibilities, permission policies, audit boundaries, and database deployment details.
- **Transitional Rules (`celery-postgresql-redesign`, until final block is DONE):** Bot, interactive CLI, and bot-facing adapters use async I/O. Native Celery workers and synchronous application use cases use synchronous SQLAlchemy/psycopg, HTTP, Telegram, and browser adapters. Create worker resources inside each child process; never inherit live pools or wrap worker execution in an event loop. Share pure policy, parsing, and rendering rules between bot and workers.

## 4. Code Conventions

- **Typing:** Strict hints required (`uv run ty check src`). Rely on Python 3.15+ deferred evaluation (PEP 649); NEVER use stringified forward refs, `# ty:ignore`, or `Any` casts to silence errors (`Any` allowed only on test mock factories).
- **Linter:** Strict Ruff (`ALL` ruleset, `uv run ruff check .`).
- **Naming & Methods:** Follow PEP 8 `snake_case` (`UpperCamelCase` allowed for `Enum` members). NEVER use `@staticmethod`, even when `self` is unused.
- **Nested Functions & Visibility:** Avoid nested/inner functions. Nested functions must be used exclusively when it is the sole and cleanest way (e.g., inline multi-line callback for filter functions). If logic represents a real operation or can be useful beyond a single inline expression, declare it as a private/protected method (`_method_name`) on the class or module level and pass via `functools.partial`.
- **Constants & Enums:** No magic numbers (extract to constants/enums; `# noqa: PLR2004` only if structural). Reference bot commands via `CommandName` and chat lifecycle states via `ChatStatus` (`StrEnum`s in `application/data/constants.py`), and HTTP statuses via `http.HTTPStatus`.
- **Exceptions:** Raise semantic built-in/custom exceptions (`ValueError`, `AttributeError`, `TypeError`). NEVER raise generic `RuntimeError`.
- **Imports:** Top-level imports only. Local imports inside functions are FORBIDDEN unless breaking circular dependencies.
- **Standard Lib & I/O:** Use `pathlib.Path` (NEVER `os.path`).
- **Serialization:** Serialize Pydantic/aiogram models via `model_dump(mode="json")` before JSON encoding; avoid `model_dump_json()` on complex aiogram types.

## 5. Testing (`PYTHONPATH=src uv run pytest`)

- **Real Infrastructure:** Test against real PostgreSQL in isolated schemas and local Valkey. Keep temporary SQLite only for historical migration and one-time import coverage. NEVER use `unittest.mock` for internal services, repositories, or persistence—verify DB/Valkey state directly.
- **Network Mocking:** Mock external HTTP APIs only (e.g., `api.vxtwitter.com`, `tikwm.com`) using `aioresponses`.
- **Zero Warnings Policy:** Test suite runs must remain clean of deprecation, resource, and framework warnings. Investigate and eliminate root causes immediately.

## 6. Commit Messages & Versioning

- **Conventional Commits:** Use standard format `<type>(<scope>): <description>`.
- **User-Facing Types (Auto-included in release notes & Telegram broadcasts):**
  - `feat`: New user-facing features or capabilities. Tagged with ✨ in notifications.
  - `perf`: Noticeable performance and extraction speed improvements. Tagged with ⚡ in notifications.
  - `!` / `BREAKING CHANGE`: Breaking architectural or user changes. Tagged with 🚨 in notifications.
- **Internal Churn Types (Filtered out from user release notes):**
  - `chore`, `test`, `ci`, `docs`, `refactor`, `style`, `build`.
- **Description Quality:** Imperative present tense (`feat(media): add...`, NOT `added...`). Concise and user-centric. Lowercase module/feature scope.
- **Post-Commit Changelog:** Post-commit hook regenerates `CHANGELOG.md` using Commitizen and amends only that file into the same commit when it changes. Hook failures cannot roll back the commit: resolve reported issues, run `make changelog`, review, and amend authorized commit.
- **Releases & Tags:** Create annotated Git tags for releases: `make tag-release VERSION=vX.Y.Z [MESSAGE="..."]`. Annotated tag messages override commit logs as curated human release notes.

## 7. Commands

- **Setup:** `make setup` (or `make setup VERSION=vX.Y.Z`)
- **Release Tag:** `make tag-release VERSION=vX.Y.Z`
- **Install Hooks:** `make install-hooks`
- **Dev Run:** `make run-dev` (complete development stack in Docker); `make dev` starts only PostgreSQL/Valkey for host-side tests
- **Prod Run / Stop:** `make run` / `make stop`
- **Test:** `PYTHONPATH=src uv run pytest`
- **Type Check:** `uv run ty check src`
- **Lint:** `uv run ruff check .`
- **Import Lint:** `uv run lint-imports`
- **AI Assistants:** `make ai TOOL=<name> [ARGS=...]` (supported tools: `agy`, `gemini`, `codex`, `claude`; see `Makefile`)
- **AI Plan Tools:** `make ai-check [SCHEME=...]` (validate plan manifests); `make ai-next SCHEME=...` (find next eligible block)

## 8. AI Workflow

Before planning, implementing, or reviewing an `ai-schemes/` initiative, load the matching procedure from `.agents/skills/`:
- **Architect:** `.agents/skills/architect/SKILL.md` (inspect current code, populate specifications, decompose into acyclic commit-sized blocks in `blocks/`, maintain contracts and `manifest.json`).
- **Implementer:** `.agents/skills/implementer/SKILL.md` (execute one block at a time, verify direct prerequisites, implement outcome and tests, write implementation report in `reports/`, update block and `STATUS.md`).
- **Reviewer:** `.agents/skills/reviewer/SKILL.md` (independently validate implementation diff against requirements and contracts, record findings under `reports/`, verify acceptance criteria).

If the assistant environment has no automated skill loader, open the matching `SKILL.md` file directly.
