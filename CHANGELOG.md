## v0.6.0 (2026-11-13)

### Feat

- **settings**: offer guided setup after chat approval
- **settings**: guide chat configuration through sequential steps
- **settings**: apply reviewed chat configuration atomically
- **settings**: add target-bound setup drafts
- **translation**: add source-bound translate buttons
- **translation**: add reply-based translate command
- **translation**: prefer free online translation with local fallback
- **translation**: add bounded Google translation and long-document segmentation
- **translation**: switch local model to MIT-licensed M2M100-418M
- **i18n**: localize administrative and error feedback
- **i18n**: localize primary bot interactive journeys
- **settings**: add personal and group language selection
- **i18n**: wire audience-aware aiogram-i18n middleware
- **i18n**: persist user and chat ui locale preferences
- **i18n**: add aiogram-i18n and fluent catalog foundation

### Fix

- **admin**: trust unseen users through Telegram selection
- **translation**: harden job cancellation, bounded HTTP responses, and snapshots
- **settings**: initialize settings_version on chat creation
- **admin**: show readable confirmation feedback
- **translation**: validate UTF-16 quote offsets and snapshot bounds
- **translation**: preserve segment separators, fallback markup, and explicit no-op feedback
- **speech**: repair private voice processing

## v0.5.1.2 (2026-11-12)

### Fix

- **queues**: route initial speech tasks to speech queue

## v0.5.1.1 (2026-11-12)

### Fix

- **media**: notify compression only when oversized and update placeholder
- **speech**: provide ephemeral storage for prefork startup

## v0.5.1 (2026-11-12)

### Fix

- **compression**: bound worker and translation memory usage
- **browser**: keep Chromium configuration in its owned profile
- **translation**: share model assets with compression workers
- **speech**: accept audio sent on behalf of a chat
- **translation**: preserve captions when decoding is incomplete
- **translation**: enforce model download deadlines during streaming
- **speech**: prepare models through a writable volume mount

## v0.5.0 (2026-11-12)

### Feat

- **speech**: finalize local recognition worker
- **speech**: add transcription commands and chat controls
- **speech**: execute and deliver admitted transcripts
- **jobs**: route speech through bounded admission
- **speech**: add on-demand local CPU inference
- **speech**: define per-chat recognition controls
- **translation**: translate captions locally with NLLB
- **media**: prepare oversized videos in a dedicated compression worker

### Fix

- **settings**: expire menus after 30 seconds of inactivity

## v0.4.0 (2026-11-08)

### Feat

- **browser**: cut over chromium adapter to pydoll sync

### Fix

- **release**: support four-part hotfix versions
- **browser**: protect SDK diagnostics and complete migration acceptance
- **jobs**: stop republishing acknowledged queued jobs every sweep
- **jobs**: reclaim running tickets abandoned by killed workers
- **core**: remediate implementation edge cases and security boundaries
- **media**: enable yt-dlp ejs/deno runtime and edit fallback message in-place
- **worker**: close opened resources when child startup fails
- **security**: reject private network destinations in worker HTTP
- **jobs**: release admission capacity when claimed work fails
- **media**: split long post text into valid Telegram messages
- **media**: escape link attributes in shared captions
- **access**: restore group access after redeployment

### Refactor

- **media**: decouple browser tab port and fix extractor scripts
- **handlers**: narrow broad Exception to TelegramAPIError in failure reply
- narrow broad Exception suppression to TelegramAPIError
- **access**: narrow broad Exception suppression to TelegramAPIError
- **operations**: narrow broad Exception suppression to TelegramAPIError

## v0.3.5.1 (2026-11-07)

### Fix

- **deps**: upgrade missing sqlalchemy dependency

## v0.3.5 (2026-11-07)

### Feat

- **instagram**: retain partial candidates across browser handoff
- **instagram**: add bounded vxinstagram extraction
- **instagram**: extract complete browser carousels

### Fix

- **media**: reuse resolved extractor key and silence timeout broken pipe
- **instagram**: bound stage extraction, retain best candidates, and strip comment URLs
- **instagram**: degrade gracefully on retained-candidate storage failure
- **instagram**: continue past text-only generic results
- **media**: bound Instagram fallback execution
- **migrations**: preserve caller logging during embedded upgrades

### Refactor

- **instagram**: remove Instaloader fallback and dependency
- **media**: declare provider extraction action sequences
- **instagram**: add typed extraction candidates and parsers
- **packaging**: partition pyproject.toml into core and role-specific extras
- **worker**: split runtime composition by role and decouple prompts from aiogram
- **media**: decouple bot ingress to provider config and delete legacy async code

## v0.3.4.1 (2026-11-03)

### Fix

- **runtime**: increase worker task timeout for browser extraction
- **logging**: preserve library diagnostics and capture stacktraces on errors
- **release**: prevent technical changelog fallback and user notification leak

## v0.3.4 (2026-11-02)

### Fix

- **media**: bound instaloader extraction time and trace backend failures

## v0.3.3 (2026-11-02)

### Fix

- **runtime**: expire stale execution budgets and optimize container lifecycle

## v0.3.2.3 (2026-11-02)

### Feat

- **deployment**: make browser builds optional and reduce disk usage

## v0.3.2.2 (2026-11-02)

### Feat

- **media**: clarify shared quotas and improve extraction diagnostics

## v0.3.2.1 (2026-11-01)

## v0.3.2 (2026-11-01)

### Fix

- **media**: report request limits and application failures
- **media**: preserve post text when video extraction fails
- **usage**: preserve reservations across quota window rollover
- **release**: commit changelog and lockfile before tagging

### Refactor

- **logging**: extend privacy-safe operational logs across the application

## v0.3.1 (2026-11-01)

### Fix

- **workers**: bound media extraction and trace failures
- **notifications**: keep combined release notes within telegram limits
- **settings**: keep notification callbacks within Telegram limits
- **build**: build shared Docker images only once

### Refactor

- **settings**: improve usage quota rendering
- **storage**: remove obsolete sqlite support

## v0.3.0 (2026-11-01)

### BREAKING CHANGE

- DATABASE_URL and PostgreSQL replace the SQLite DB_PATH runtime.
Stop old writers and run the documented one-time import with the original
encryption configuration before starting against the new database.

### Feat

- **queue**: notify users about delayed and expired requests
- **audit**: inspect administrative change history
- **recovery**: restore records with explicit lifecycle choice
- **recovery**: preview explicit entity recovery choices
- **lifecycle**: soft-delete user and group identities
- **announcements**: confirm urgent notification bypass
- **announcements**: dispatch confirmed routine broadcasts
- **announcements**: compose encrypted scoped broadcast drafts
- **notifications**: announce feature outages by preference
- **beta**: announce new features with activation choices
- **notifications**: deliver through the operations worker
- **notifications**: add scoped notification preferences
- **admin**: control global feature emergency stops
- **features**: persist emergency stop state and epochs
- **usage**: show scoped feature usage and allowances
- **admin**: manage scoped feature usage limits
- **queue**: bound and deduplicate work admission
- **limits**: reserve execution budgets atomically
- **usage**: persist idempotent usage aggregates
- **limits**: define scoped feature usage policies
- **cli**: add access and feature administration
- **admin**: manage access and beta grants privately
- **settings**: manage groups from private bot chat
- **settings**: expose group feature and access settings
- **beta**: authorize whole-group feature activation
- **beta**: add explicit feature eligibility grants
- **groups**: preserve permissions across Telegram group migration
- **access**: activate groups through explicit trust
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

- **persistence**: enforce advisory locks retry loops and schema parity
- **queue**: retain cancelling worker budgets and enforce feedback gates
- **operations**: harden notification delivery and escape html payloads
- **identity**: correct chat type inference recovery order and route lookups
- **settings**: preserve session tokens on alerts and optimize group discovery
- **security**: enforce admin ban checks and atomic session tokens
- **access**: restore middleware denial prompts and beta feature enforcement
- **release**: correct handle resolution and log filtering
- **audit**: close mutation coverage and privacy gaps
- **features**: clear queued work on emergency disablement
- **admin**: preserve ban reason validation message
- **tests**: isolate worker valkey databases away from db 0
- **cache**: distinguish missing messages from storage failures
- **tests**: prevent standard library module shadowing
- **settings**: bind callbacks to verified target context
- **features**: enforce eligibility across discovery and execution
- **audit**: validate entity and action fields atomically
- **privacy**: keep payloads out of operational logs
- **privacy**: minimize authorized message caching
- **access**: separate trust from settings authority
- **access**: require fresh administrator snapshots
- **access**: enforce suspension and protect admin authority
- **validation**: preserve valid translation data and simplify guards
- **encryption**: require configuration and prevent plaintext caching
- **telegram**: use DeliveryError.TOO_LARGE in optimistic URL failure
- **routing**: replace substring matching with strict aiogram Command filters
- **access**: restrict chat approval/rejection callbacks to bot owner
- **language**: return defensive copies of ignored languages
- **notifications**: preserve HTML parse mode and isolate retry failures
- **di**: remove duplicate AccessCommand provider

### Refactor

- **typing**: eliminate production Any annotations and deduplicate helpers
- **lifecycle**: preserve durable configuration deletion history
- **updates**: use recipient-aware release notifications
- **notifications**: track per-recipient delivery state
- **architecture**: enforce the final package contracts and close
- **bootstrap**: split composition providers by ownership
- **telegram**: organize Telegram transport and delivery
- **infrastructure**: group runtime and external adapters
- **persistence**: consolidate PostgreSQL persistence packages
- **translation**: group translation ownership
- **media**: organize provider and media I/O ownership
- **application**: group capability application code
- **application**: group access and identity application code
- **deploy**: separate worker and database services
- **queue**: remove unused module-level Celery app
- **media**: route work through native worker stages
- **maintenance**: move background work to Celery
- **queue**: reconcile native worker lifecycle
- **workers**: compose native Celery execution
- **media**: support synchronous Instagram and Threads extraction with browser continuation
- **media**: support synchronous API and HTML provider extraction
- **tests**: organize suite by application architecture layers
- **tests**: use real Telegram models and remove container alias
- **tests**: consolidate provider trigger cases
- **tests**: move shared helpers into structured tests utils package
- **tests**: remove dead methods and deduplicate legacy mock tests
- **clean-code**: verify complete application coverage and strict acceptance
- **bootstrap**: review sqlite import pipeline and admin entrypoint
- **bootstrap**: decompose application startup and teardown
- **bootstrap**: group providers and extract worker resource accessors
- **telegram**: separate media and feedback interface dispatch
- **telegram**: separate access denial handling
- **settings**: separate language picker rendering
- **admin**: separate private transport guards
- **release**: separate notification and changelog processing
- **cleanup**: separate expired message processing
- **activation**: separate administrator evidence lookup
- **commands**: share provider split resolution
- **settings**: separate session validation guards
- **trust**: separate administration authority checks
- **admin**: separate target resolution and dispatch
- **access**: separate group management evidence checks
- **access**: separate target and group decisions
- **policy**: separate group fact queries
- **http**: retire optional native wrappers
- **activation**: separate authorization and dispatch
- **commands**: share explicit selection mutation
- **beta**: separate enabler reconciliation
- **access**: separate ban authority and mutation
- **membership**: separate snapshot reconciliation
- **tests**: type recorded response registration
- **identity**: separate public handle synchronization
- **identity**: separate group migration operations
- **usage**: simplify policy persistence
- **usage**: simplify ledger transactions
- **audit**: split transaction attempts and lock preparation
- **clean-code**: complete telegram delivery recovery
- **clean-code**: decompose telegram delivery and semantic recovery
- **browser**: separate lifecycle attempts and retain operation ownership
- **translation**: separate provider failure from display policy
- **media**: flatten semantic strategy recovery and finalization
- **media**: require usable extraction and download results
- **providers**: separate browser quote and media eligibility
- **providers**: separate community parsing and post attribution
- **providers**: separate quote text and media selection rules
- **providers**: separate browser candidates and media selection
- **http**: add strict native reads and bounded transport chunks
- **payloads**: share required encryption and metadata validation
- **budgets**: separate replay and atomic reservation writes
- **budgets**: separate record binding and resource specifications
- **admission**: distinguish unavailable state from missing work
- **admission**: decompose optimistic transaction attempts
- **admission**: separate record validation and transition preconditions
- **admission**: separate ingress validation and policy resolution
- **usage**: separate policy resolution and accounting validation
- **policy**: separate evidence and eligibility decisions
- **features**: separate job and operation invariants
- **usage**: separate reservation and policy invariants
- **audit**: separate sanitization policy and field validation
- **media**: separate native worker transports
- **infrastructure**: replace Valkey Lua scripts with native Redis transactions
- **queue**: make admission the lifecycle owner
- **browser**: rank Unix browser installation targets
- **workers**: add native persistence and Celery foundations
- **database**: migrate persistence to PostgreSQL
- **queue**: define encrypted feature job envelopes
- **audit**: use standard strict payload validation
- **features**: decouple selection state from audit history
- **features**: make activation state explicit
- **features**: introduce stable feature registry
- **bans**: enforce restrictions without leaving groups
- **access**: enforce action-specific authorization
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

- **limits**: validate bounded media execution defaults
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
