# HovorunV2

**The ultimate wingman for your Telegram group chats — making them smarter, faster, and way more fun.**

HovorunV2 is a feature-rich Telegram chatbot designed to level up your group conversations with smart utilities, dynamic
translations, and seamless media integrations.

---

## 🚀 Project Status: Alpha

HovorunV2 is currently in its **Alpha** stage.

### How to use Hovorun:

1. **Public Instance**: You can add the bot to your group and use `/allow_chat` to join our **waiting list**. Bot owners
   will review and approve requests based on current capacity.
2. **Self-Hosting**: If you don't want to wait or prefer full control over your data, you can host your own instance
   using Docker.

---

## ✨ Features for Users

### 📼 Universal Media Extraction

Stop clicking links! Hovorun automatically detects and expands content from major platforms:

* **Twitter (X)**: Rich previews with full text, media carousels, quoted posts, and engagement metrics.
* **Instagram**: Native support for Reels and **mixed-media carousels** (Photos + Videos).
* **TikTok**: High-quality video previews and image slideshows.
* **Reddit**: Full support for text posts, images, multi-image galleries, videos, crossposts, and shortlinks (`/s/`, `redd.it`).
* **YouTube**: Seamless expansion for videos (`/watch`, `youtu.be`), Shorts, and **Community Posts** (text and multi-image attachments).
* **Threads, Bluesky, Facebook**: Clean, descriptive previews for posts, videos, and quoted threads.

### 🌍 Dynamic Translation

Keep the conversation flowing regardless of the language. Hovorun automatically translates foreign-language content into
the chat's target language (default: Ukrainian).

* **Ignore List**: Configure specific languages to be left as-is.

### ⚙️ Interactive Configuration & Privacy

* **`/settings`**: A unified interactive menu to configure language settings and independently toggle **Video (`🎬`)** vs. **Post/Image (`🖼️`)** capture for every media provider in your chat.
* **⌛ Self-Cleaning UI**: Configuration windows automatically self-destruct after 30 seconds of inactivity to keep your
  chat history clean.
* **🔐 Encrypted Message Cache**: Ephemeral cached messages in Valkey are encrypted per chat using unique salts and Fernet symmetric encryption.

### 🚀 Auto-Update Notifications

Stay in the loop! Approved groups automatically receive "What's New" alerts whenever we deploy new features.

---

## 🛠️ Getting Started (Self-Hosting)

If you choose to self-host, deployment is fully automated via `Make` and `Docker`.

### Prerequisites

* `docker` & `docker compose`
* `make`

### Quick Start

1. **Prepare**: `make setup` (initializes repo and builds optimized images).
2. **Configure**: Edit the `.env` file created during setup:
   ```env
   BOT_TOKEN=...
   ADMIN_IDS=[123, 456]    # Global Admins
   OWNERS=[123]            # Owners (for approval requests)
   ```
3. **Launch**: `make run`

---

## 💻 For Developers

### Tech Stack

* **Runtime**: Python 3.15+ (latest features)
* **Framework**: [aiogram 3.x](https://github.com/aiogram/aiogram)
* **DI Container**: [dishka](https://github.com/reagento/dishka) for constructor injection
* **Persistence**: SQLAlchemy 2.0 (Async) + SQLite
* **Caching**: Valkey (Async) for session state, TTLs, and encrypted message cache
* **Automation**: [DrissionPage](https://github.com/g1879/DrissionPage) for advanced scraping

### Key Principles

* **Strict Typing**: Zero `ty:ignore` policy with Python 3.15+ deferred evaluation.
* **Layered Onion Architecture**: Strict Ports & Adapters enforced via `import-linter`.
  * `domain`: Pure SQLAlchemy models and enums.
  * `application`: Protocols (`ports.py`), orchestration services (`services/`), resilient 4-tier media processing (`media/pipeline.py`), and extraction clients (`clients/`).
  * `infrastructure`: Adapters and stores (`persistence/`, Valkey `CacheService`, `BrowserService`, `TaskQueueService`).
  * `interface`: Telegram delivery adapter (`TelegramMediaDelivery`), declarative commands (`BaseCommand(ABC)`), and handlers.
  * `bootstrap`: Centralized composition root (`bootstrap/container.py`).
* **Zero Runtime Leaks**: Mandatory dependency injection; pure queries without scattered transaction logic.

### Development Commands

* `make run-dev`: Local bot execution with Valkey in Docker.
* `uv run ty check src`: Strict type verification.
* `uv run ruff check .`: Linting and formatting.
* `uv run lint-imports`: Enforce onion architecture layer contracts.
* `PYTHONPATH=src uv run pytest`: Run unit and integration test suite.
* `make install-hooks`: Install quality, Conventional Commit, and post-commit changelog hooks.

After each commit, the post-commit hook generates `CHANGELOG.md` with Commitizen and amends
that file into the same commit if needed. The amendment preserves the message, author, and
unrelated staged changes; hooks are disabled only for that generated-file amendment to avoid
recursion. Install hooks in each checkout before committing.

The hook refuses to overwrite local changelog edits or amend a tagged commit. A post-commit
failure leaves the original commit in place: resolve the reported issue, run `make changelog`,
review the generated changes, and amend the commit before tagging or pushing.

---

## 🤝 AI Assistance Policy

Using AI tools (Copilot, ChatGPT, Claude, Cursor, etc.) for writing code is allowed. However:
**The commit author takes full and absolute responsibility for the code.** You must review, understand, and test every
line generated by AI. "The AI wrote it" is not an acceptable excuse for architectural flaws, hallucinations, or failing
pipelines. Treat AI as an assistant, but *you* are the engineer.

---

## 📄 License

Released under the **BSD 3-Clause License**. See `LICENSE` for details.

Developed with ❤️ by TwilightSparkle42

### Offline role administration

Authority uses ordered `Role` integer values: `USER = 1`, `ADMIN = 10`, `OWNER = 100`. Minimum authority checks use
`role >= Role.ADMIN`; exact role checks remain available. Trust, bans, group membership and feature access stay separate.

Prepare an empty or existing database with `uv run alembic upgrade head`, then use the standalone CLI:

```bash
uv run hovorun-admin role add 123456789 --role owner --reason "Initial bootstrap"
uv run hovorun-admin role add @known_user --role admin
uv run hovorun-admin role list
uv run hovorun-admin role list --role owner
uv run hovorun-admin role remove 123456789 --yes --reason "Retired operator"
```

The example ID is synthetic. Numeric provisioning works before the user contacts the bot. Handles must already resolve to
an active stored user; group, unknown, and deleted-user handles are rejected. Removing authority sets the user's role to
`USER`. Both removal and replacing an `OWNER` with `ADMIN` require `--yes`. Repeating an assignment does not duplicate audit
entries. Role changes record CLI origin and the optional reason in the same transaction.

The CLI and migrations require only `DB_PATH` (default `data/bot.db`, relative to the project root). They do not authenticate
to Telegram, start polling, initialize browsers, or require Valkey or encryption credentials. Run migrations before
administration; the CLI does not create schema or restore deleted users.

To administer the persistent database without starting the bot or Valkey:

```bash
docker compose --profile prod run --rm --no-deps bot uv run hovorun-admin role add 123456789 --role owner
docker compose --profile prod run --rm --no-deps bot uv run hovorun-admin role list
docker compose --profile prod run --rm --no-deps bot uv run hovorun-admin role remove 123456789 --yes
```

The container entrypoint applies migrations first. Use `docker compose exec bot uv run hovorun-admin ...` when the bot
container is already running. These commands prepare database roles; live bot authorization still uses the existing
configuration until the planned authorization cutover. No configured owner/admin IDs are imported automatically.

### Permission policy foundation

Protected actions can be evaluated through `AccessService.evaluate_permission` with an application-owned `PermissionRequest`.
The service reads current database authority on each call, checks active identities and group generations/bans, and then
uses the packaged Casbin policy. Group administrator evidence is scoped to the actor, target generation and validity window;
missing or stale evidence grants no group administrator authority. Bot administration requires explicit private transport.

Static capabilities live in `src/hovorunv2/infrastructure/authorization/policies/`. The initial actions are
`bot:administer` and `group:settings`; unmatched actions deny. Policies use the existing integer authority tiers and remain
immutable after loading. Add a policy row and focused tests for a new action; introduce new domain facts and guards only
when that action needs them. There is no separate policy database or user-role mirror. CLI bootstrap keeps its explicit
application-service path.

This foundation is additive. Existing handlers still use the legacy authorization path until the planned cutover;
Telegram membership verification, sponsor eligibility, additional ban types and feature/emergency guards remain separate
implementation stages. Request facts are internal application inputs, never proof supplied by callback payloads.
