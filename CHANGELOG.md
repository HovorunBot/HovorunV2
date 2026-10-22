## Unreleased

### Feat

- **notification**: add direct GitHub release tag link and filter internal scopes
- **queue**: implement SAQ task queue, periodic chat reminders, and /feedback command
- **ci**: add Git pre-commit hooks, ruff formatting, and classify post-tag commits as unpublished
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
