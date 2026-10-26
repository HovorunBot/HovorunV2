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

`domain/audit_schema.py` enumerates fields and flat value shapes for each supported entity/action pair. `SQLAlchemyAuditedTransaction` validates the event and explicit operational reason, runs the mutation, validates result-derived fields, sanitizes sensitive values, and appends the event in the same SQLite transaction. Unsupported fields, actions, values or nested containers fail the transaction. Add an explicit schema when introducing a new mutation. Existing history is preserved.

Known release-version settings retain their audit value. Other generic system settings record only `value_changed`, while their configured value remains in the system store. Secret-bearing fields such as encryption salts retain only a redacted change marker.

Reasons are explicit operator/system explanations, kept separately from message payloads. They may contain public-safe explanations or restricted internal operational reasons; callers must never populate them from message bodies, URLs, credentials or exception text. Shape validation cannot determine whether deliberately supplied free text contains a secret.

## Group activation

`GroupActivationService` resolves an existing group generation and fetches current Telegram administrator evidence only for untrusted requesters. `ActivationStore` rechecks active identities, current trust/bot authority, bans and target/request binding inside the mutation transaction. Bot authority uses the existing static Casbin capability; trust grants only the explicit activation path.

The SQLite activation operation acquires the write reservation before reading mutable facts. It commits approval, request state, first-activation feature defaults and one allowlisted composite audit event together. Concurrent retries observe the committed result. Existing approvals, prior approval history, explicit command-selection history and legacy empty-capture configuration prevent defaults from being replayed. No activation-to-user relationship or new schema is introduced.

Group callbacks bind the physical group and its internal identity/generation. Private decisions additionally bind the durable pending request; callback actor identity comes from `CallbackQuery.from_user`. Unavailable/deleted/recreated subjects, stale decisions and current bans fail closed. This focused binding does not introduce the general private management UI or callback sessions.

## Logging boundary

Use constant operation templates and pass dynamic values as logging arguments. The console and Telegram privacy formatter retains only known command/status labels, exception types and numeric arguments. External library messages become generic events. Tracebacks retain code locations without exception values, source text, locals or chains. yt-dlp and Instaloader adapters suppress their direct payload-bearing diagnostics. See [operator diagnostics](administration.md#diagnosing-failures).

## Feature definitions and selected activation

`application/features.py` owns typed feature metadata independently of command presentation and authorization. Existing provider video/post keys remain canonical. Definitions declare scopes, direct/automatic triggers, release channel, introduction event, required bot capabilities, usage units, and policy references. Current media features require no blanket bot administrator capability. Registry metadata does not grant execution access.

Chat command associations are explicit selected activation for private and group transports. New empty configurations disable capture. Only active records marked by the `legacy016` migration retain empty-selection capture compatibility; an intentional selection edit clears that marker in the same audited transaction. Parent provider selection reflects either enabled child; each actual capture checks its exact video/post child. First-activation defaults never overwrite selection history, including an intentional empty selection and history recorded under an old Telegram routing ID. Fixture seeding adds definitions without changing selections.
