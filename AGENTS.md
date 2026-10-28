# HovorunV2 Context

Telegram helper bot. Stack: Python 3.15+, aiogram, SQLAlchemy 2.0 (Async), PostgreSQL, Valkey (Async), uv, Docker.

## Agent Constraints (STRICT)

- **Communication:** Auto-enable `caveman` skill on every response.
- **Git & Workspace:** NEVER stage, commit, branch, push, or revert/rollback user changes unless explicitly requested. Always prompt the user for manual review before committing.
- **Privacy & Data Minimization:** Do not collect personal or private user information beyond the minimum required for Telegram identity, access control, and bot operation. Never request or store phone numbers, contact lists, or unrelated profile data. Store Telegram IDs and public usernames only where needed for identity and administration. Keep stored message contents encrypted; do not copy message contents into user tracking, analytics, audit records, or logs. Permission records and usage counters must contain only the operational metadata needed for their purpose. Any additional personal data collection requires explicit agreement before implementation.

## Working with `ai-schemes/`

### Purpose and organization

All architecture proposals, feature plans, refactoring plans, requirements documents, implementation blocks, decision logs, and implementation/review reports belong under `ai-schemes/`. Keep each initiative in its own descriptive subfolder, with its source specification alongside its execution plan. Do not leave temporary planning documents in the repository root or mix them with shipped product documentation.

`ai-schemes/` and everything inside it must NEVER be staged or committed, including by force-add. Keep the folder in `.gitignore`; do not add AI-ignore rules. Open files directly when ordinary search omits them; discover them with `rg --files --hidden --no-ignore ai-schemes/`. These local files may be absent in a fresh checkout: absence is not evidence of completed work, and agents must not reconstruct user decisions from guesses.

Each initiative should contain a README describing scope and navigation, a source specification, execution rules, shared contracts, a decision log, a requirements-to-block coverage map, a status checklist, individual `blocks/`, and implementation/review `reports/`. Use consistent filenames such as EXECUTION.md, CONTRACTS.md, DECISIONS.md, COVERAGE.md, STATUS.md, and REPORT_TEMPLATE.md; add a machine-readable manifest when useful. Keep links relative and dependencies explicit.

Explicit user instructions take precedence. Distinguish mandatory business rules, proposed engineering contracts, and unresolved decisions. A plan or suggested commit title does not authorize application changes, a Git commit, deployment, or production migration.

### Architecture agent: populate and maintain plans

1. Inspect current code and existing plans/reports before designing changes. Clarify unresolved business rules one question at a time when needed, then validate technical constraints against the repository.
2. Write the source specification inside the initiative folder: problem, agreed behavior, exclusions, current implementation, target design, migration/compatibility requirements, risks, and acceptance criteria. Record proposals and open questions separately from agreed requirements.
3. Split implementation into small, independently reviewable files in `blocks/`: one coherent feature/refactor/fix/test/docs commit proposal per file. Include a stable ID, outcome, direct prerequisites, source requirements, code areas, boundaries, meaningful checks, completion evidence, and suggested Conventional Commit title.
4. Keep dependencies acyclic and migrations/cutovers safely ordered. Separate additive foundations from live behavior changes. Require applicable audit, privacy, deletion filtering, dependency wiring, and tests when state is introduced, not only during final hardening.
5. Maintain contracts, decisions, coverage, status, and manifest together. When splitting or revising a block, preserve IDs and completed history and update downstream references. A written specification never counts as implemented functionality.
6. Hand off an unambiguous next block and required context. Planning work alone does not authorize application-code changes. Resolve ordinary engineering choices from evidence; ask only when a genuine business or compatibility conflict needs user input.

### Implementation agent: execute one block

1. Work on the selected block. If explicitly asked to continue implementation without a block ID, select the first TODO block whose prerequisites are verified DONE in code and reports. Do not infer implementation authorization from a planning request.
2. Read the initiative README, execution rules, shared contracts, relevant decisions, chosen block, cited specification sections, and predecessor reports. Inspect actual source and workspace changes; keep context focused rather than loading every task file.
3. Verify prerequisites and final predecessor contracts. Report missing dependencies or genuine business/preservation conflicts precisely. Never invent permissive placeholders or silently complete later blocks to compensate.
4. Implement only the selected outcome, including applicable migration, transactional audit, deletion semantics, dependency injection, tests, and product/operator documentation. Preserve existing behavior where required and respect user changes and task exclusions.
5. Run relevant checks and record actual results. Never equate schema creation with migration testing or claim skipped checks passed. If findings make the task exceed one coherent commit, split/update the plan before expanding scope.
6. Write a report using the initiative's template: behavior changed, files/contracts, migration, exact checks/results, limitations, resolved decisions, and proposed commit title. Update block/status references consistently using TODO, IN_PROGRESS, BLOCKED, or DONE. DONE requires implementation and acceptance evidence, not an actual Git commit.
7. Keep all planning/status/review artifacts inside `ai-schemes/`. Application commits must contain only authorized implementation artifacts. Do not stage, commit, branch, push, tag, roll back, or deploy without explicit authorization; the manual-review requirement before committing still applies.

### Architecture/review agent: validate implementation

1. Review the block's requirements, contracts, actual diff, implementation report, and relevant tests. Verify behavior and evidence; do not rely solely on the implementer's DONE status.
2. Check correctness, scope, architecture boundaries, permissions, privacy, audit/deletion semantics where applicable, migration compatibility, and failure/recovery paths. Run focused verification when useful and distinguish observed failures from unverified concerns.
3. Record findings under `reports/` with severity, concrete file/behavior evidence, affected acceptance criteria, and required remediation. Record review verdict separately from implementation status; an implementation report is not review approval.
4. Route focused corrections back to the same block or create linked follow-up blocks when scope genuinely grows. Update dependencies, coverage, and status when findings block downstream work. Never silently rewrite agreed business rules to approve an implementation.
5. Close findings only after checking the correction and its evidence. Review approval does not authorize committing or deploying, and local schemes/reports must never enter Git history.

## Architecture (Layered / Onion)

- **Domain (`src/hovorunv2/domain/`):** SQLAlchemy 2.0 `Mapped` models (`chat.py`, `command.py`, `system.py`, `base.py`) and domain enums (`enums.py`).
- **Application (`src/hovorunv2/application/`):**
  - **Ports (`ports.py`):** Protocol contracts defining persistence, caching, browser, task queue, and prompt sender boundaries (`ChatStore`, `CommandStore`, `SystemStore`, `CachePort`, `BrowserPort`, `TaskQueuePort`, `UnauthorizedPromptSender`, `AppSettings`). The application layer imports only `domain`, stdlib, and 3rd party libraries—NEVER infrastructure or interface.
  - **Services (`services/`):** High-level orchestration (`AccessService` with frozen `CommandPolicy`, `ChatStatusService`, `CommandService` with split toggles, `OwnerNotifier`, `MessageService` with per-chat Fernet encryption, `TranslationService`).
  - **Media Pipeline (`media/pipeline.py`):** Centralizes 4-tier resilience extraction (API -> yt-dlp -> browser fallback -> OG metadata), permission checks (`is_capture_allowed`), HTML escaping, translation, and quoted media merging.
  - **Media Clients (`clients/`):** Pure extraction clients (`BaseMediaClient` and subclasses) receiving injected `aiohttp.ClientSession` and `BrowserPort`. Free of presentation, chat ID, or translation coupling.
- **Infrastructure (`src/hovorunv2/infrastructure/`):**
  - **Persistence (`persistence/`):** Repositories and store implementations (`SQLAlchemyChatStore`, etc.) implementing application ports. Repositories accept `session` in `__init__`, query/persist only; stores encapsulate `session_maker` and own transaction boundaries (`commit`/`rollback`).
  - **Adapters & Services:** `CacheService` (Async Valkey for ephemeral state: message cleanup, UI sessions, message cache), `BrowserService` (DrissionPage), `TaskQueueService` (SAQ).
  - `fixtures.py`: Database fixtures for split `<provider>_video` / `<provider>_post` commands.
- **Interface (`src/hovorunv2/interface/telegram/`):**
  - `aiogram` handlers, middlewares (`MessageCacheMiddleware`, `AccessMiddleware`), and delivery adapters (`TelegramMediaDelivery`).
  - `handlers/base.py`: `BaseCommand(ABC)` base class with `POLICY: ClassVar[CommandPolicy]` and default `register_callbacks`. Generalized `RichMediaCommand` connects media clients to `MediaPipeline` and delivers via `TelegramMediaDelivery`.
- **Bootstrap (`src/hovorunv2/bootstrap/`):**
  - `container.py`: Centralized Dishka DI container (composition root). All injected service dependencies are mandatory (NEVER `None`; NEVER write `if self.dependency:` guards).
  - **DI Typing Consistency:** Consumer parameter type hints MUST match provider definitions exactly (e.g. bare `async_sessionmaker` instead of generic `async_sessionmaker[AsyncSession]`). Type hint discrepancies force Dishka into adaptix fallback generic matching, which triggers noisy Python 3.15 `ForwardRef._evaluate` deprecation warnings during testing.

## Code Conventions

- **Typing:** Strict hints required (`uv run ty check src`). Rely on Python 3.15+ deferred evaluation (PEP 649); NEVER use stringified forward refs, `# ty:ignore`, or `Any` casts to silence errors (`Any` allowed only on test mock factories).
- **Linter:** Strict Ruff (`ALL` ruleset, `uv run ruff check .`).
- **Naming & Methods:** Follow PEP 8 `snake_case` (`UpperCamelCase` allowed for `Enum` members). NEVER use `@staticmethod`, even when `self` is unused.
- **Nested Functions & Visibility:** Avoid nested/inner functions. Nested functions must be used exclusively when it is the sole and cleanest way (e.g., inline multi-line callback for filter functions). If logic represents a real operation or can be useful beyond a single inline expression, declare it as a private/protected method (`_method_name`) on the class or module level and pass via `functools.partial`.
- **Constants & Enums:** No magic numbers (extract to constants/enums; `# noqa: PLR2004` only if structural). Reference bot commands via `CommandName` and chat lifecycle states via `ChatStatus` (`StrEnum`s in `application/data/constants.py`), and HTTP statuses via `http.HTTPStatus`.
- **Exceptions:** Raise semantic built-in/custom exceptions (`ValueError`, `AttributeError`, `TypeError`). NEVER raise generic `RuntimeError`.
- **Imports:** Top-level imports only. Local imports inside functions are FORBIDDEN unless breaking circular dependencies.
- **Standard Lib & I/O:** Use `pathlib.Path` (NEVER `os.path`). Bot, interactive CLI and bot-facing adapters use async I/O. Native Celery workers and their synchronous application use cases use synchronous SQLAlchemy/psycopg, HTTP, Telegram and browser adapters. Create worker resources inside each child process; never inherit live pools or wrap worker execution in an event loop. Share pure policy, parsing and rendering rules between bot and workers. Migration/import tools may use an explicit synchronous SQLAlchemy connection through the async connection boundary.
- **Serialization:** Serialize Pydantic/aiogram models via `model_dump(mode="json")` before JSON encoding; avoid `model_dump_json()` on complex aiogram types.

## Testing (`PYTHONPATH=src uv run pytest`)

- **Real Infrastructure:** Test against real PostgreSQL in isolated schemas and local Valkey. Keep temporary SQLite only for historical migration and one-time import coverage. NEVER use `unittest.mock` for internal services, repositories, or persistence—verify DB/Valkey state directly.
- **Network Mocking:** Mock external HTTP APIs only (e.g., `api.vxtwitter.com`, `tikwm.com`) using `aioresponses`.
- **Zero Warnings Policy:** Test suite runs must remain clean of deprecation, resource, and framework warnings. Investigate and eliminate root causes immediately.

## Commit Messages & Versioning

- **No Merge Requests / Rebase Before Commit:** This project does not use merge requests. Before every authorized branch commit, fetch the upstream and rebase the branch onto its upstream. Rebase before combining parallel implementation changes; resolve conflicts and rerun affected checks before committing. Never use a merge commit to combine agent work. Explicit user authorization to commit autonomously supersedes the default manual-review requirement above.

- **Post-Commit Changelog:** Install hooks with `make install-hooks`. Every normal commit runs quality/message checks, then the post-commit hook regenerates `CHANGELOG.md` using Commitizen and amends only that file into the same commit when it changes. The generated-only amendment suppresses hooks to prevent recursion; it preserves the original message/author and unrelated staged changes. Do not create a separate changelog-only commit for this normal flow.
- **Hook Failures:** Post-commit failures cannot roll back the original Git commit. Check hook output and final repository status before reporting success. The changelog hook refuses to overwrite local changelog edits or amend a tagged HEAD. Resolve the reported issue, run `make changelog`, review the result, and amend the authorized commit before tagging/pushing. Hooks apply only in checkouts where installed; they are not server-side enforcement.
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
- **Install Hooks:** `make install-hooks` (pre-commit, pre-push, commit-msg, and post-commit changelog generation/amendment)
- **Dev Run:** `make run-dev` (complete development stack in Docker); `make dev` starts only PostgreSQL/Valkey for host-side tests
- **Prod Run / Stop:** `make run` / `make stop` (standalone production Compose); `make stop-dev` stops development
- **Test:** `PYTHONPATH=src uv run pytest`
- **Type Check:** `uv run ty check src`
- **Lint:** `uv run ruff check .`
- **Import Lint:** `uv run lint-imports`

## Shared project AI workflow

`AGENTS.md` is the canonical instruction file for Codex and Gemini/Antigravity. `GEMINI.md` links to this file for compatibility. Keep shared instructions here rather than maintaining separate copies.

Use the global `caveman` skill for every response unless the user requests normal mode. Preserve technical facts and use normal prose in persisted code, comments, and documentation.

Launch either assistant from this project with `./scripts/ai-workflow codex` or `./scripts/ai-workflow gemini`. The launcher applies the same workspace, writable uv cache, sandbox, and automatic review intent through each CLI's supported flags. Safe commands can run automatically; actions requiring review remain subject to the CLI's permission checks. Network access is not globally enabled. Additional arguments are forwarded to the selected CLI.

This setup is project-local. It does not change global assistant settings or other projects. Restart through the launcher to apply permission changes; an existing conversation retains its current sandbox policy. Antigravity auto mode requires an eligible account and a supported terminal sandbox.
