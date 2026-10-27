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

`domain/audit_schema.py` declares allowed fields for each business event using strict Pydantic type adapters. Standard Python scalar, list, nullable and annotated pair types replace a separate field/shape validation language. Identical event payloads share declarations. `SQLAlchemyAuditedTransaction` validates the event and explicit operational reason, runs the mutation, validates result-derived fields, sanitizes sensitive values, and appends the event in the same SQLite transaction. Unsupported fields, actions, values or nested containers fail the transaction. Declare allowed fields when introducing a new mutation; reuse existing adapters and payload declarations where applicable. Existing history is preserved.

Known release-version settings retain their audit value. Other generic system settings record only `value_changed`, while their configured value remains in the system store. Secret-bearing fields such as encryption salts retain only a redacted change marker.

Reasons are explicit operator/system explanations, kept separately from message payloads. They may contain public-safe explanations or restricted internal operational reasons; callers must never populate them from message bodies, URLs, credentials or exception text. Shape validation cannot determine whether deliberately supplied free text contains a secret.

## Group activation

`GroupActivationService` resolves an existing group generation and fetches current Telegram administrator evidence only for untrusted requesters. `ActivationStore` rechecks active identities, current trust/bot authority, bans and target/request binding inside the mutation transaction. Bot authority uses the existing static Casbin capability; trust grants only the explicit activation path.

The SQLite activation operation acquires the write reservation before reading mutable facts. It commits approval, request state, first-activation feature defaults and one allowlisted composite audit event together. Concurrent retries observe the committed result. Existing approvals, prior approval history, explicit chat configuration intent and legacy empty-capture configuration prevent defaults from being replayed. Configuration intent lives on the immutable chat identity; activation does not read audit history or create an activation-to-user relationship.

Group callbacks bind the physical group and its internal identity/generation. Private decisions additionally bind the durable pending request; callback actor identity comes from `CallbackQuery.from_user`. Unavailable/deleted/recreated subjects, stale decisions and current bans fail closed. This focused binding does not introduce the general private management UI or callback sessions.

## Logging boundary

Use constant operation templates and pass dynamic values as logging arguments. The console and Telegram privacy formatter retains only known command/status labels, exception types and numeric arguments. External library messages become generic events. Tracebacks retain code locations without exception values, source text, locals or chains. yt-dlp and Instaloader adapters suppress their direct payload-bearing diagnostics. See [operator diagnostics](administration.md#diagnosing-failures).

## Beta entitlements

Beta eligibility uses `BetaEligibilityService`, the `BetaEntitlementStore` boundary and a session-bound repository.
`BetaEntitlementDB` references internal user/group identities; an explicit `*` grant represents all beta features for a
user. Group grants always name one feature. Reads filter revoked/deleted grants and deleted subjects. Typed,
feature-bound `BetaEligibilityFacts` feed eligibility evaluation independently of selected activation. Grants remain in
SQLite and are never mirrored into Casbin. The store reserves the SQLite writer before rechecking current actor
role, private transport and exact target generation; entitlement mutation and minimal audit commit atomically.

`GroupBetaService` keeps group beta selection separate from current eligibility. An eligible, currently verified Telegram
administrator can select a beta feature and record their own support. Other eligible administrators can add support to the
same selection. Reads require a current user grant and fresh administrator evidence for at least one recorded enabler;
membership or eligibility loss suspends only that feature without changing selection, group approval or standard features.
An explicit group entitlement can supply eligibility independently of local enablers, but never selects a feature by itself.
Bot administrators and CLI can select using that entitlement; bot-admin authority alone supplies no beta eligibility.

Selection changes recheck current actor, exact group identity/generation, grants, membership and Casbin settings authority
inside the audited SQLite transaction. Disabling revokes the selection's supporting enablers; later local activation records
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
