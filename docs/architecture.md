# Architecture

[Back to README](../README.md) · [Contributor guide](../CONTRIBUTING.md)

## Layers

| Layer | Responsibility |
| --- | --- |
| `domain` | SQLAlchemy models, enums and domain validation |
| `application` | Persistence/adapter protocols, services, media extraction and orchestration |
| `infrastructure` | Session-bound repositories, transaction-owning stores, Valkey, browser and queue adapters |
| `interface` | Telegram handlers, middleware, prompts and media delivery |
| `bootstrap` | Composition root and mandatory dependency injection |

Import-linter checks dependencies between layers. Application code depends on domain and application-owned ports, with adapters supplied by the composition root. Repositories query and persist within a provided session; stores own transaction boundaries.

## Permission policy

Protected actions can be evaluated through `AccessService.evaluate_permission` with an application-owned `PermissionRequest`.
The service reads current database authority on each call, checks active identities and group generations/bans, and then
uses the packaged Casbin policy. Group administrator evidence is scoped to the actor, target generation and validity window;
missing or stale evidence grants no group administrator authority. Trust never grants settings authority. Until target-bound private group menus are implemented, ordinary users must manage settings inside the group, where current membership is checked. Bot administration requires explicit private transport.

Static capabilities live in `src/hovorunv2/infrastructure/authorization/policies/`. The initial actions are
`bot:administer` and `group:settings`; unmatched actions deny. Policies use the existing integer authority tiers and remain
immutable after loading. Add a policy row and focused tests for a new action; introduce new domain facts and guards only
when that action needs them. There is no separate policy database or user-role mirror. CLI bootstrap keeps its explicit
application-service path.

Live role gates use database authority through Casbin. Request facts are internal application inputs, never proof supplied by callback payloads. Feature/emergency guards are separate concerns.

Trust grant/revoke accepts an existing active numeric user ID or known public handle. It never creates or recovers identities. Trust remains separate from roles and beta eligibility; each actual change atomically records before/after values and advances the audited user trust revision. Repeating the same setting produces no extra audit event or revision. Telegram service callers require current bot-admin authority and private transport; trust alone grants no administrative powers.

## Audit boundary

`domain/audit_schema.py` declares allowed fields for each business event using strict Pydantic type adapters. Standard Python scalar, list, nullable and annotated pair types replace a separate field/shape validation language. Identical event payloads share declarations. `SQLAlchemyAuditedTransaction` validates the event and explicit operational reason, runs the mutation, validates result-derived fields, sanitizes sensitive values, and appends the event in the same PostgreSQL transaction. Unsupported fields, actions, values or nested containers fail the transaction. Declare allowed fields when introducing a new mutation; reuse existing adapters and payload declarations where applicable. Existing history is preserved.

Known release-version settings retain their audit value. Other generic system settings record only `value_changed`, while their configured value remains in the system store. Secret-bearing fields such as encryption salts retain only a redacted change marker.

Reasons are explicit operator/system explanations, kept separately from message payloads. They may contain public-safe explanations or restricted internal operational reasons; callers must never populate them from message bodies, URLs, credentials or exception text. Shape validation cannot determine whether deliberately supplied free text contains a secret.

## Group activation

`GroupActivationService` resolves an existing group generation and fetches current Telegram administrator evidence only for untrusted requesters. `ActivationStore` rechecks active identities, current trust/bot authority, bans and target/request binding inside the mutation transaction. Bot authority uses the existing static Casbin capability; trust grants only the explicit activation path.

The PostgreSQL mutation locks the group parent and shares the actor fact lock before reading mutable facts. It commits approval, request state, first-activation feature defaults and one allowlisted composite audit event together. Concurrent retries observe the committed result. Existing approvals, prior approval history, explicit chat configuration intent and legacy empty-capture configuration prevent defaults from being replayed. Configuration intent lives on the immutable chat identity; activation does not read audit history or create an activation-to-user relationship.

Group callbacks bind the physical group and its internal identity/generation. Private decisions additionally bind the durable pending request; callback actor identity comes from `CallbackQuery.from_user`. Unavailable/deleted/recreated subjects, stale decisions and current bans fail closed. This focused binding does not introduce the general private management UI or callback sessions.

## Logging boundary

Use constant operation templates and pass dynamic values as logging arguments. The console and Telegram privacy formatter retains only known command/status labels, exception types and numeric arguments. External library messages become generic events. Tracebacks retain code locations without exception values, source text, locals or chains. yt-dlp and Instaloader adapters suppress their direct payload-bearing diagnostics. See [operator diagnostics](administration.md#diagnosing-failures).

## Beta entitlements

Beta eligibility uses `BetaEligibilityService`, the `BetaEntitlementStore` boundary and a session-bound repository.
`BetaEntitlementDB` references internal user/group identities; an explicit `*` grant represents all beta features for a
user. Group grants always name one feature. Reads filter revoked/deleted grants and deleted subjects. Typed,
feature-bound `BetaEligibilityFacts` feed eligibility evaluation independently of selected activation. Grants remain in
PostgreSQL and are never mirrored into Casbin. The transaction locks the target parent and current actor facts before rechecking role, private transport and exact target generation; entitlement mutation and minimal audit commit atomically.

`GroupBetaService` keeps group beta selection separate from current eligibility. An eligible, currently verified Telegram
administrator can select a beta feature and record their own support. Other eligible administrators can add support to the
same selection. Reads require a current user grant and fresh administrator evidence for at least one recorded enabler;
membership or eligibility loss suspends only that feature without changing selection, group approval or standard features.
An explicit group entitlement can supply eligibility independently of local enablers, but never selects a feature by itself.
Bot administrators and CLI can select using that entitlement; bot-admin authority alone supplies no beta eligibility.

Selection changes recheck current actor, exact group identity/generation, grants, membership and Casbin settings authority
inside the audited PostgreSQL transaction. Disabling revokes the selection's supporting enablers; later local activation records
support again. Personal bans prevent human configuration changes, while existing group-owned support follows eligibility
and membership. Existing group access evaluation applies group bans, full-banned administrators, verification and approval.
Availability requires an explicit emergency-disabled guard, with no permissive default. Emergency persistence and protected
execution/menu integration are separate follow-up blocks; current registry definitions remain standard media features.

`FeatureAccessService` is the shared application evaluator for feature discovery, settings and execution. It checks
registry scope/trigger, current identities and restrictions, private access or live group approval, beta eligibility,
explicit selection and independently declared bot capabilities. Group execution uses current group beta support rather
than requiring a personal grant for every member. Automatic group work retains group-owned access when its sender has a
personal restriction; explicit commands and callbacks check the human actor.

Telegram middleware rejects unknown protected feature keys and checks provider split availability before dispatch.
`MediaPipeline` checks availability before extraction and rechecks the identified split after extraction, before translation
and delivery. Settings filter registry definitions using configuration authority and eligibility; guessed or stale toggles
repeat the same application check. Group beta changes retain the transactional 029 store checks. Standard toggles propagate
the authenticated actor into existing transactional audit events. Public help and approval/feedback links remain available;
feature listings include only currently executable definitions. Missing capability/access guidance is exposed only to a
currently authorized manager. No feature emergency-state storage or queued-worker implementation is introduced here.

## Feature definitions and selected activation

`application/features.py` owns typed feature metadata independently of command presentation and authorization. Existing provider video/post keys remain canonical. Definitions declare scopes, direct/automatic triggers, release channel, introduction event, required bot capabilities, usage units, and policy references. Current media features require no blanket bot administrator capability. Registry metadata does not grant execution access.

Chat command associations are explicit selected activation for private and group transports. New empty configurations disable capture. Only active records marked by the `legacy016` migration retain empty-selection capture compatibility; an intentional selection edit clears that marker in the same audited transaction. Parent provider selection reflects either enabled child; each actual capture checks its exact video/post child. The `features_configured` chat flag records configuration intent independently of audit retention and Telegram routing IDs. Migration `legacy016` initializes it from existing selections and preserved legacy compatibility. First-activation defaults never overwrite an intentional empty selection. Fixture seeding adds definitions without changing selections.

## Database deployment

PostgreSQL and Valkey run independently of application processes with named data volumes. Every application process owns its connection pools. Audited mutations use scoped parent/row locks and conflict-safe PostgreSQL writes. Shared actor locks permit independent target mutations to progress concurrently. Missing-key gates protect ingestion and other absent rows; no application-wide writer lock remains. See the [complete writer lock matrix](postgresql-concurrency.md). See [database setup](postgresql.md).

After migrations and fixture initialization, the bot refreshes one bounded batch of due membership evidence before polling
and readiness. The startup batch checks at most ten groups within a thirty-second deadline; remaining
groups stay due for the operations worker. A matched group action requiring approval also refreshes missing or expired
administrator and bot snapshots before access evaluation. This fallback has a fifteen-second deadline, coalesces concurrent
actions per group, and records a thirty-second retry cooldown in Valkey. Fresh snapshots, private actions, public commands,
and unmatched messages do not trigger verification API calls. Failed verification retains approval and cannot grant access;
normal policy evaluates the persisted evidence after the attempt. Verification is runtime initialization, not a migration.


## Package ownership

The five layers remain fixed and import-linter enforced. Application code is grouped by capability: `access/`, `identity/`, `groups/`, `settings/`, `features/`, `usage/`, `jobs/`, `media/`, `translation/`, `messaging/`, and `operations/`, with shared `configuration.py`, `auditing.py`, and `data/`. Application protocol declarations live with the capability that owns them; `application/ports.py` no longer exists. Infrastructure groups adapters by technology: `persistence/{stores,repositories}/`, `runtime/`, `queues/`, `browser/`, `http_transport/`, `authorization/`, and `config.py`. Interface Telegram transport and settings modules live under `interface/telegram/{transport,settings}/`. Bootstrap keeps `container.py` as the central composition entrypoint and owns provider classes under `bootstrap/providers/`. Root `hovorunv2/utils/` holds reusable technical helpers (`crypto.py`, `formatting.py`, `metadata.py`, `text.py`); every layer may import utils, while utils imports no layer. The import-linter enforces both the layer order and utils independence. Concrete media-provider transport relocation remains deferred where application callers would require new protocols. Translation uses application-owned detector and engine ports with local infrastructure adapters.

## Worker redesign contracts

The following process ownership is the implemented production architecture. Media, browser, operations, and lifecycle recovery run in native synchronous Celery workers; Beat owns periodic operations; the bot owns only Telegram ingress, interactive behavior, admission, and publication.

| Process | Responsibilities |
| --- | --- |
| Async bot | Telegram update ingestion, interactive commands/settings/feedback, current-authority checks, bounded startup/action membership verification, bounded admission and publication |
| Native synchronous media worker | Fresh authority and budget checks, API/yt-dlp extraction, post delivery and video preparation handoff |
| Native synchronous browser worker | Browser continuation, lazy child-owned Chromium, post delivery and video preparation handoff |
| Native synchronous compression worker | Video delivery, split-stream merging and bounded oversized-video compression |
| Native synchronous operations worker | Bounded reminders, membership reconciliation, cleanup, notifications and lifecycle/accounting recovery |
| Singleton Beat | Publish periodic operations; no business mutations or per-bot scheduler |
| Explicit migration/import tool | Schema upgrades and verified one-time transfer without bot/browser initialization |

PostgreSQL owns durable business records, audit and accounting. Valkey owns broker traffic and bounded encrypted runtime state. The admission ticket owns execution claims, stages and terminal state; queue transport never creates a second receipt ledger or bypasses admission. Celery supplies delivery, routing and native process pools. Each child creates and closes its own database/network/browser resources. No worker asyncio pool, asyncio.run wrapper or live dependency passed from the bot is permitted.

Bot, media, browser, compression, operations, singleton Beat and migration run as independent Compose services with direct `/app/.venv/bin/*` entrypoints from dedicated role images (`hovorunv2-migrate`, `hovorunv2-bot`, `hovorunv2-worker-ops`, `hovorunv2-worker-media`, `hovorunv2-worker-browser`, `hovorunv2-worker-compression`). Each runtime role includes only its required optional dependencies (e.g. `aiogram` only in `bot`; `yt-dlp` in media-capable workers; `pydoll-python` in browser and compression workers; `alembic` only in `migrate`). Runtime composition is partitioned by `WORKER_ROLE` (`operations`, `media`, `browser`, `compression`). `BROWSER_ENABLED` defaults to false; Make selects the browser build target and Compose profile only when enabled in the selected environment. Disabled runtime workers reject browser continuation before queue handoff and settle failed extraction with zero consumption. The browser target installs Chromium at build time and starts it lazily inside a browser worker child. Beat stores its schedule in a private named volume, and no embedded scheduler or worker starts with the bot.

Every extracted video hands off to the `compression` stage before delivery. Its dedicated Celery task is
`run_compression_job`, routed to `work:{work}:compression`; both Compose stacks start `worker-compression` with
prefork concurrency two and prefetch one. The existing Valkey execution reservation remains held across the handoff:
the default global concurrency ceiling of two applies across worker replicas, including administrator requests.
Cancellation, original deadlines, current access checks and duplicate-execution fences remain admission-owned.
A deduplicated preparation notice tells the requester that oversized videos will be compressed before delivery.

Video handoffs store bounded encrypted metadata in the existing candidate store; neither broker arguments nor stored
candidates contain video bytes. Quoted posts and source URLs survive the handoff. Browser-backed video downloads are
refreshed in an isolated authenticated browser context inside the compression child, using retained media identities.
Consequently the compression image derives from the browser image and launches Chromium lazily only when needed;
this trades a second browser download for avoiding a shared plaintext media spool or large broker/Valkey payloads.
The compression worker is required even when browser fallback is disabled.

The common downloader preserves selected split streams by downloading their source page through yt-dlp and merging
with FFmpeg before upload. Missing audio fragments fail the operation rather than yielding a silent fallback.
Oversized videos from every provider, including transient browser buffers, use two-pass H.264/AAC encoding with a
49,000,000-byte output ceiling. Small files remain unchanged. Processing bounds include a 200 MiB source allowance
(combined across split-stream transfers), ten-minute duration limit, 4096-by-2160 input pixel ceiling, two codec
threads, 45-second compression budget and 90-second download-batch budget. Encoding uses at most 1280-by-720 output,
30 fps and 4 Mbit/s video, retaining a 200 kbit/s video floor and 64 kbit/s audio budget. Local processing rejects
network/playlist protocols, removes metadata and temporary files, and checks output size, duration and audio
presence before accepting compressed output. Limits or processing failures produce an explicit failure response;
photos retain their existing delivery policy. FFmpeg and FFprobe are installed in the media, browser and compression
runtime images.

The browser adapter uses `pydoll.sync` through the synchronous application port. Its internal `pydoll-sync-loop`
daemon starts on first use in the worker child and resets after a fork; it does not change Celery's native execution
model. Module-level lazy SDK imports keep bot, migration, media, operations and Beat imports isolated. `OwnedChromium`
launches the exact subprocess, reads its DevToolsActivePort WebSocket address and attaches without delegating process
or profile ownership to the SDK. The initial tab remains an anchor; each extraction gets a disposable browser context
and tab, isolating cookies and local storage. SIGALRM enforces the shared extraction deadline and fractional per-call
budgets. Expiry kills Chromium before SDK connection teardown. A missing required wait selector raises BrowserError.
The known pydoll 3.0.1 coroutine-inspection deprecation on Python 3.15 is explicitly accepted; other warnings remain fatal.

Shared values include PermissionRequest/PermissionTarget/PermissionFacts/AccessDecision, FeatureDefinition/FeatureDecision, BetaEligibilityFacts/GroupBetaFacts, UsageSubject/UsagePolicy/ResolvedUsage, and strict JobEnvelope/SealedPayload metadata. These are inputs/results, not transferable permission grants. Re-read authority, generation, route and feature state before execution and delivery. Extract pure evaluators from asynchronous fact collection; share provider parsing and formatting independently of HTTP/Telegram transports. No generic sync/async compatibility framework or unused port is required.

The versioned job boundary contains only bounded feature/trigger, actor when applicable, target identity/generation, charged subject, message/deduplication identifiers, original acceptance/deadline and encrypted-payload references. It contains no Message/Bot, ORM entity, session, browser, copied allow decision or plaintext private content. Direct work requires an active human identity; automatic group work is group-owned. JobEnvelope and AdmissionService accept actor_id=None and actor_generation=None for automatic group work, including sender_chat attribution. Direct work still requires both positive actor fields. RichMediaCommand ingress admits the request and publishes the thin Celery envelope; extraction, delivery, and accounting happen in native workers.

Removal work is complete: R06 consolidated queue receipt state into admission; R09 replaced the transitional in-process bridge with native worker tasks; R10 moved media extraction and delivery into staged workers; R12 moved maintenance to operations tasks and singleton Beat; R13 split lean, browser, and importer image targets and removed the exec-only shell entrypoint; R14 and R15 aligned tests and proved restart/fault isolation. Immediate interactive replies remain with the bot.

### Shared policy persistence and worker database ownership

`FeatureAccessService` evaluates immutable `FeatureFacts` using pure feature rules. The bot's
`SQLAlchemyFeatureFactStore` reads those facts through SQLAlchemy `AsyncSession.run_sync`: statements still use the async
psycopg driver, without blocking I/O or a second event loop. Native `WorkerPolicyStore` uses the same session-bound
`PolicyFactReader` and evaluator with synchronous SQLAlchemy sessions. Current user roles, active identities, group
approval/blockers, beta support, selections and capabilities come from PostgreSQL on each call. Historical role rows and
previous allow results never grant authority. Permission and usage resolution rules are also shared pure functions.

`bootstrap.worker_database.WorkerDatabase` is the native database composition boundary. Constructing it opens no engine
or connection. Enter its context inside the worker child, retain it for that child's lifetime, and exit it on shutdown.
It owns a synchronous engine with one pooled connection and no overflow, native policy/accounting adapters and packaged
Casbin policy. Access outside the open owning process, including retained adapter references after fork or shutdown, fails.
Contexts are single-use. No bot container, polling loop or browser package is needed by this composition. Celery
lifecycle signals connect this boundary to child-local resources, and `worker_app` starts no resource until a worker
child begins executing.

`WorkerPolicyStore.active_target` validates exact identity, generation and canonical route using the same statements as
bot beta/usage persistence. Worker execution must bind envelope references and recheck current policy at its required
execution/delivery boundaries; a discovery result alone is not an execution authorization. No job contract changes here.

Both runtimes use one session-bound accounting repository for receipt claims, replay fingerprint checks and guarded atomic
aggregate upserts. Their transaction owners commit receipt, totals and one allowlisted audit summary together. Only
serialization/deadlock failures retry the database-only transaction, at most three times with fresh sessions. Synchronous
accounting uses the same trusted-origin and supported-dimension validation; no external effect runs in its transaction.
Accounting for retained deleted identities remains supported without restoring their access. These reads add no private
payload, profile data or new persistence state. The ORM base inherits DeclarativeBase directly without AsyncAttrs
or greenlet, using explicit eager selectin relationships across domain models.

### Outbound destination policy and media transport security

Native worker media extraction and downloads enforce public-only network destination policies across direct HTTP requests and downstream backend tools. `NativeHTTPClient` validates target URLs and resolves destination hosts against private, loopback, link-local, multicast, unroutable, and cloud metadata IP ranges. URL validation enforces safe schemes (HTTP/HTTPS only), rejects embedded user credentials and non-standard/forbidden ports, and enforces public-only destination addresses on every redirect hop at socket connect time. Test-specific network hosts are permitted only when explicitly provided in `allowed_hosts`.

Downstream yt-dlp fallback downloads enforce the same public-only destination policy prior to backend invocation, disable local file URL resolution (`enable_file_urls: False`), restrict format protocols to HTTP(S) and HLS m3u8 streams, enforce execution deadlines and socket timeouts, and track streamed bytes via progress hooks to terminate downloads exceeding bounded byte limits. Media transport and extraction diagnostics log generic error codes and omit target URLs and sensitive query parameters.


## Admission runtime ownership

Admission stores the counted encrypted payload and pending capacity atomically. Duplicate incoming actions retain the
original ticket and deadline. Provider ingress accepts either permitted content split and uses the shared media pending
policy; native execution must recheck the exact extracted split before delivery and charging.

The same ticket owns MEDIA and BROWSER stage claims. Each claim has an exclusive token and increasing attempt number.
A stage handoff retains pending capacity and ciphertext, records recoverable browser work, and fences late acknowledgements
by stage and attempt. Deadline expiry cannot release a running owner's resources. Cancellation of active work requests
cooperation; only verified terminal completion releases its counted resources. Publication without admission fails closed.

`bootstrap.worker_runtime.WorkerRuntime` creates synchronous Valkey clients only when entered inside their owning process.
Its admission, encrypted payload and execution budget adapters reject retained references after close or fork. Sender
attribution stays inside bounded short-lived ciphertext and appears only in the in-process decrypted payload. This runtime
composition does not register Celery tasks; R09 owns native task composition and R10 owns handler cutover.


Admission, execution budgets and encrypted payload CAS use native Valkey commands. Conditional multi-key admission and
budget changes use bounded WATCH/MULTI/EXEC transactions. Both runtimes share typed persisted records and pure decision
calculations; each adapter owns its concrete asynchronous or synchronous I/O. Missing or malformed active resource
metadata fails closed. Payload SET NX writes and equality checks never extend the original retention on replay. No embedded
Valkey Lua, second runtime receipt ledger or PostgreSQL executable queue is required.

### Native waiting and terminal settlement

`WorkerLifecycleService` owns native quota waiting recovery and terminal reconciliation. A quota-denied worker records
`WAITING` with an eligibility time, releases its execution claim, and returns immediately. Admission counts, ciphertext,
original acceptance, and deadline remain unchanged. Valkey sorted sets rotate charged-subject/provider lanes and tickets
within each lane. New lanes and tickets join the rotation's tail; a flooded user or group cannot continually jump ahead.
Each sweep examines at most 100 lanes. Empty lanes disappear with the final ticket's terminal release. Recovery publication
uses a stage/attempt compare-and-set and a five-second retry guard. Duplicate or uncertain broker publication remains safe
because consumers must acquire the exclusive admission claim.

The admission ticket records acknowledged budget ownership across browser handoff. Queued cancellation or expiry of a
continuation first records zero-consumption settlement; it cannot discard a held execution reservation. Active cancellation
retains its owner until the worker records stopped effects. Immediately before Telegram delivery, the owner atomically
checks cancellation and deadline and records `DELIVERY_UNCERTAIN`. Missing acknowledgements, partial delivery, unexpected
execution exceptions, and child loss cannot cause an automatic refund or another send. Native worker logs use fixed safe
messages, job IDs, stages, timing, and allowlisted failure reasons. Exception traces include only types and code locations,
never exception values, provider URLs, credentials, or message contents. See [worker diagnostics](worker-diagnostics.md)
for request tracing and optional owner alerts from both worker parent and child processes.

After verified stopped effects, the owner records an immutable `SettlementIntent` on the same ticket. It contains only the
safe outcome, measured units, and an optional technical usage event. Recovery replays the PostgreSQL receipt first, settles
Valkey consumption second, then releases admission counts/bytes and deletes the encrypted payload. Either backend commit
may be repeated after a crash. Event identity, charged subject, timestamp, amounts, and ownership are validated; the
existing PostgreSQL receipt fingerprint prevents duplicate debits. Missing runtime ownership fails closed. A retained
terminal ticket is replayed without renewing receipt or deduplication retention.

Delayed and terminal feedback use ticket-bound intent flags and atomic `claim_notice` dispatch claims. Each kind can be
claimed once across bot and workers; delayed feedback cannot be claimed after work leaves waiting. Dispatch claims precede
external sends, so uncertain acceptance cannot request duplicate feedback. No rendered notice body is stored. Handler
integration retains its separate cutover boundary.

The synchronous Celery task `recover_feature_jobs` runs one bounded sweep using child-owned resources. Its Beat scheduling
and production operations-service wiring belong to the maintenance cutover. It does not start a scheduler in the bot.
Before deploying this ticket format, hold admission and drain tickets and reservations from older workers. Do not mix
worker versions or delete runtime keys to manufacture a drain. No PostgreSQL migration is required.

A crash between external delivery and recording settlement remains delivery-uncertain. A lost child without stopped-effect
proof also remains held, even after its deadline. Operators must verify execution/browser descendants and delivery facts
before resolving these holds; Celery results and elapsed time are insufficient. Recovery must run within existing runtime
receipt/resource retention. If required resources have disappeared, automatic reconciliation retains admission capacity
rather than assuming unused consumption or resetting budget windows.

## Local caption translation

`NativeTranslationService` reads current PostgreSQL target/ignored preferences for each caption. Pure normalization lives in `domain/languages.py`; translation owns Unicode detection sanitization, source eligibility, and FLORES script mapping. Local py3langid detection gates the engine before any model provisioning or inference. Unknown, ignored, target-language, and insufficient-evidence captions skip translation. No caption text is sent to a remote translation service.

Media/browser worker children construct mandatory detector/engine dependencies. Native CTranslate2 and SentencePiece imports and model allocation are deferred until supported inference is required. The owning child fences inference and shutdown by PID and releases resources through its ExitStack. Operations, bot, and migration roles do not import translation infrastructure or its native dependencies.

The model store streams immutable, size/SHA-256-checked assets into temporary files under a bounded exclusive file lock, then atomically publishes them. Incomplete files are removed; corrupted cached artifacts are repaired. A persistent `translation_models` volume is mounted at `/app/data/models` in media and browser workers. `TRANSLATION_MODEL_DIR` selects the artifact directory. Provisioning/download failures are semantic `TranslationError` failures, recovered by the application by preserving the original caption. Invalid/unsupported input is rejected before model loading.

The code remains BSD-3-Clause. NLLB weights remain separately licensed CC BY-NC 4.0 with full license and attribution copied beside every provisioned model. See [third-party notices](../THIRD_PARTY_NOTICES.md). The selected model is for the current noncommercial stage; commercial use requires a model/license review. Source lines over 512 tokens and captions over the configured fixed bounds skip optional translation. Output is bounded to 256 decoding tokens per nonblank line; paragraph breaks are preserved.
