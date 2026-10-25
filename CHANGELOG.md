## Unreleased

### Feat

- **access**: preserve group grants and reconcile membership evidence
- **trust**: add audited trust administration
- **access**: add Casbin policy foundation
- **cli**: bootstrap and administer authority roles offline
- **roles**: persist one CLI-managed authority role per user
- **identity**: resolve public handles without transferring authority
- **identity**: preserve durable group identity and metadata
- **identity**: persist lifecycle-aware users
- **audit**: audit durable mutations in one transaction
- **command**: collapse legacy parent provider toggles via alembic migration
- **notification**: extract OwnerNotifier to unify owner broadcasting
- **command**: add is_capture_allowed helper to CommandService

### Fix

- **validation**: preserve valid translation data and simplify guards
- **encryption**: require configuration and prevent plaintext caching
- **telegram**: use DeliveryError.TOO_LARGE in optimistic URL failure
- **routing**: replace substring matching with strict aiogram Command filters
- **access**: restrict chat approval/rejection callbacks to bot owner
- **language**: return defensive copies of ignored languages
- **notifications**: preserve HTML parse mode and isolate retry failures
- **di**: remove duplicate AccessCommand provider

### Refactor

- **access**: evaluate independent group access rules
- **access**: remove sponsorship and organize migration tests
- **roles**: replace configured administrator lists
- **access**: convert CommandPolicy to frozen dataclass and BaseCommand to ABC
- **cleanup**: remove unused callbacks, ensure_admin, and use settings.github_url in notifications
- **media**: decouple clients, introduce MediaPipeline, and simplify RichMediaCommand
- **media**: decouple MediaExtractor from translation and chat presentation
- **telegram**: extract TelegramMediaDelivery from RichMediaCommand
- **media**: consolidate downloaded bytes into MediaItem
- **services**: drop redundant platform parameter from application service APIs
- **di**: move container to bootstrap, eliminate module-level settings and bot router
- **domain**: type chat status and platform enums and use JSON for ignored_langs
- **interface**: decouple reminder service from telegram prompt presentation
- **application**: split utils into browser_discovery, crypto, and text modules
- **infra**: replace ChatRepository.save with pure session add
- **infra**: move data services to infrastructure persistence stores
- **application**: introduce ports and decouple application services from infrastructure
- **domain**: move constants and enums to domain layer

### Perf

- **encryption**: cache PBKDF2 Fernet keys and derive in thread pool

## v0.2.1 (2026-10-23)

### Feat

- **queue**: implement SAQ task queue, periodic chat reminders, and /feedback command
- **ci**: add Git pre-commit hooks, Commitizen check, and code quality formatting
- **versioning**: automate Git tag releases, build verification, and group update notifications
- **youtube**: improve community posts handling, canonicalization, and yt-dlp skip logic

## v0.2.0 (2026-10-22)

### Feat

- **providers**: add Reddit via vxReddit and YouTube posts support with tests
- **logging**: track user_id and chat_id across update lifecycle and message cache
- implement image processing command
- improve messages encryption

### Fix

- upgrade dependencies and resolve issues with tests
- permissions for toggable commands

### Refactor

- **media**: unify media clients and commands with 4-tier extraction and split modes
- rename whitelisted with status enum
- configuration and approval logic now more user friendly.

## v0.1.1 (2026-10-22)

### Feat

- implement cached messages encription

### Fix

- annoying errors spam

## v0.1.0 (2026-10-22)

### Feat

- implement global approval logic and toggable configuration
- fetch metrics and footer from threads
- enhance threads processing using playwright
- generalize behaviour of all media commands response
- add support for handling Bluesky media links
- add support for instagram and facebook posts
- add command configuration support for Telegram bot
- draft implementation of threads post parsing
- introduce media extractor service and new platform commands
- migrate caching system to Redis with Docker support
- integrate alembic migrations and per-chat translation settings

### Fix

- resolve issue with notification service
- resolve issue with instagram age verification
- ensure media extraction works against all providers
- browser connection is lost for threads
- missing playwright during docker build
- ensure twitter and bluesky copy media from quoted tweets
- ensure instagram loader correctly works with regular posts
- enhance Bluesky client to handle redirects and improve URL pattern matching
- ensure that text is captured in case of long captions
- ensure os can write to the database
- prevent overriding of .env file
- ensure application rebuilding during commands running
- run update on `deploy` command
- reset initial migration and enhance deployment commands
- add data folder to git-ignore
- remove irrelevant music part for tiktok payload

### Refactor

- replace playwright with DrissionPage
- introduce DI using dishka library
- streamline Telegram bot logic, introduce middlewares for message caching and whitelist enforcement
- restructure application layers and enhance service dependencies
- improve translation service robustness and session management
- extract media services and overhaul testing infrastructure
- unify rich media handling and improve code quality
