# Administration

[Back to README](../README.md)

## Offline roles and trust

Each user has one authority role: USER, ADMIN or OWNER. Trust, bans, group membership and feature access remain separate.

Prepare an empty or existing database with `uv run alembic upgrade head`, then use the standalone CLI:

```bash
uv run hovorun-admin role add 123456789 --role owner --reason "Initial bootstrap"
uv run hovorun-admin role add @known_user --role admin
uv run hovorun-admin role list
uv run hovorun-admin trust grant @known_user --reason "Trusted user"
uv run hovorun-admin trust revoke 123456789 --reason "Trust withdrawn"
uv run hovorun-admin role list --role owner
uv run hovorun-admin role remove 123456789 --yes --reason "Retired operator"
```

The example ID is synthetic. Numeric provisioning works before the user contacts the bot. Handles must already resolve to
an active stored user; group, unknown, and deleted-user handles are rejected. Removing authority sets the user's role to
`USER`. Both removal and replacing an `OWNER` with `ADMIN` require `--yes`. Repeating an assignment does not duplicate audit
entries. Role changes record CLI origin and the optional reason in the same transaction.

Only CLI-origin operations may change `ADMIN`/`OWNER` authority or apply/revoke personal restrictions against them.
Non-CLI deletion cannot remove their authority. Group bans and suspension caused by a full-banned current Telegram
administrator still block feature processing in that group for everyone, including bot administrators and owners.
Those group restrictions preserve administrators' private communication and authorized private administration;
explicit personal restrictions applied through CLI remain effective.

The CLI and migrations require only `DATABASE_URL` (PostgreSQL with psycopg). They do not authenticate
to Telegram, start polling, initialize browsers, or require Valkey or encryption credentials. Run migrations before
administration; the CLI does not create schema or restore deleted users.

To administer the persistent database without starting the bot or Valkey:

```bash
make compose-run ARGS="--no-deps bot uv run --no-sync hovorun-admin role add 123456789 --role owner"
make compose-run ARGS="--no-deps bot uv run --no-sync hovorun-admin role list"
make compose-run ARGS="--no-deps bot uv run --no-sync hovorun-admin role remove 123456789 --yes"
```

The dedicated `migrate` service applies migrations first. The one-off command works whether or not the bot is running. Add `COMPOSE_ENV=dev` for development. Live administrator and owner checks use these database roles through Casbin. No owner/admin IDs are imported from
configuration; legacy `ADMIN_IDS` and `OWNERS` values are ignored and can be removed. An empty role registry grants nobody
administrator access. Owner notifications and optional error DMs follow current active OWNER rows without restarting.

## Diagnosing failures

Operational logs and owner error alerts retain operation names, known provider/command/status labels, numeric metadata, exception types and traceback module/function/line locations. Use these fields to locate the failed operation and distinguish HTTP, extraction, browser and delivery failures. Arbitrary library messages are replaced with a generic event.

Logs exclude message URLs, arbitrary string arguments, exception values, source lines, locals and exception chains. Reproduce content-specific failures locally with a deliberately supplied URL. Never paste credentials or private message contents into issue reports or operator reasons.

## Group access

Use `/allow_chat` or the access button in the target group. Explicitly trusted users and bot administrators can activate it directly; trusted users need no Telegram administrator rights. Other Telegram administrators/creators submit an approval request. Ordinary untrusted members cannot submit one.

New requests notify active bot ADMIN and OWNER recipients privately. Approve or reject using that notification; decisions recheck current authority, bans, group identity and pending request. Old buttons cannot authorize a different or recreated group. After upgrading from the older approval buttons, run `/allow_chat` in the group to refresh the request.

Approval, request decisions, first-activation defaults and actor provenance are saved together. Repeated activation preserves feature selections, language settings and encryption salts. Later trust loss, departure, ban or deletion of the activating user does not revoke approval. A newly approved group still waits for required membership verification before processing features.

Notifications are attempted after the database commit. Delivery failures do not undo the request, and repeated commands do not duplicate it or resend the initial notification. Pending-request reminders continue through the existing maintenance cycle; a durable delivery queue is not part of this workflow.

Groups need approval and fresh, successful administrator verification. Missing, failed, future or expired evidence pauses processing without revoking approval. Group bans and full bans on current Telegram administrators suspend group processing for everyone. A ban on an ordinary member does not suspend unrelated members. Private bot administration remains available to authorized bot administrators, subject to any personal restriction applied through CLI.

Membership evidence expires after five minutes. The operations worker refreshes up to ten due groups per minute, with up to fifty relevant member checks per group and a thirty-second cycle deadline. Telegram requests have ten-second deadlines. Unprocessed evidence remains expired; runtime full-ban checks cover all retained administrator candidates. Ordinary messages do not trigger administrator-list API calls.

## Beta entitlements

Beta eligibility is separate from trust, authority roles and selected features. The application administration service
accepts all-beta or individual beta grants for an existing active user, and specific-feature overrides for an existing
active group. Mutations require CLI origin or a current bot administrator acting in private transport. An all-beta user
grant includes future beta definitions, with no introduction-date cutoff. A group override never grants all future beta
features.

Granting eligibility does not enable features, modify group approval, or bypass group restrictions. Revocation takes
effect on the next eligibility read and retains grant and selection history. Deleted identities and deleted grants never
provide eligibility. Public administration menus and CLI grant commands belong to later administration blocks; the
application services and storage do not introduce a production beta feature.

Whole-group beta selection records supporting eligible Telegram administrators. Once selected and supported, ordinary
members need no personal beta grant to use that group feature. If the final supporting administrator loses eligibility or
current administrator status, the feature pauses while its selection stays saved. Another recorded qualifying enabler keeps
it available. Restored eligibility and verified administrator evidence restore availability automatically. Group approval and
standard media settings stay independent.

A specific group grant supplies eligibility independently of local enablers. A bot administrator or CLI must still select
the feature explicitly; grants never enable all future beta features. Group bans, full-banned administrators, uncertain group
verification and emergency disablement still block availability. Explicitly disabling a beta feature revokes recorded support,
so a later local activation records supporting administrators again. Settings and help now filter feature discovery through the same application policy used by execution. Guessing a
feature command or an old module callback cannot bypass current eligibility, selection or group restrictions. An eligible
administrator can configure an inactive beta feature; ordinary users see available selected features. Missing bot rights or
access verification produce guidance only for authorized managers. Public help, approval requests and feedback links remain
available even when no feature can be used.

## Feature selections

Feature selection is explicit: disabling every media feature keeps capture disabled, including after repeated approval or restart. Existing migrated empty selections retain their documented compatibility until an intentional feature setting edit. Private and group configuration follows the same selection rules. Provider toggles control their video/post children; enabling one child does not enable the other.

## Group restrictions

Application bans and rejected access requests do not make the bot leave a group. Join, rejoin and maintenance preserve restrictions and approvals. Restricted groups cannot process features, even for bot administrators. A denied group message may receive fixed guidance to contact an administrator privately, limited to one attempt per group and restriction reason every 60 seconds. Internal ban reasons never appear in that guidance.

## Settings windows

Settings windows expire after 30 seconds. Reopen `/settings` after expiry or a bot restart that clears the cache. Each click consumes the current menu token and replaces the keyboard; old or copied buttons cannot change another group or window. Opening a new settings window invalidates the previous window in that chat.

Every settings action checks the current actor and target again. Group administrators lose access immediately when Telegram reports demotion; bot administrators use their current persisted authority. Anonymous sender-chat identities cannot open user-specific settings. Language and feature changes retain the authenticated actor in their transactional audit.

Private selected-group context is available to later management menus. It binds the private actor, selected internal group identity and generation independently of the private transport chat. This does not add group-selection menus or broaden existing private management access.

Group settings include an Access & permissions view for current managers. It explains processing availability, bot verification and eligible feature capability requirements without disclosing internal restriction reasons. Modules retains language-independent video/post controls and explicit eligible beta activation. Bot-wide trust, ban and role controls are not exposed in groups.

## Private group management

Use `/settings` privately to select a known group. Ordinary users see only groups where the bot is present and Telegram currently verifies them as an administrator. Bot administrators can inspect all known active groups, including restricted groups and groups where the bot is absent. Private chats, channels and deleted group identities are excluded.

Group discovery scans at most eight candidates per page. A page can be empty while More groups leads to later candidates. Selection and every later action recheck the current actor and exact group; demotion, departure, failed verification, deletion or routing/generation changes invalidate the relevant action. The menu expires after its original 30-second deadline.

Private group settings reuse the same language, feature selection and audited beta activation operations as in-group settings. Private group-administration access does not grant private media access or bypass group processing restrictions. Eligible current group administrators can explicitly support group beta activation in either transport; bot administrators without local qualifying support still need an explicit group feature grant. No role-assignment controls are added.

Private access administration is available to current bot administrators through `/admin`.
Use `/admin inspect user 900001` or `/admin inspect group -900001` to inspect an existing identity.
Known public handles may replace numeric IDs. Unknown handles never create records.

Mutations first display the exact numeric target and generation, then require a single-use
confirmation within 30 seconds. The confirmation belongs to that private actor and message;
current authority and target identity are checked again. Pending confirmation data is encrypted.
Role assignment and demotion remain available only through the offline CLI.

Examples use synthetic IDs:

```text
/admin trust user 900001
/admin untrust user @known_user
/admin ban user 900001 --ban-kind personal --reason "operator investigation" --public "Contact administrators"
/admin unban user 900001 --ban-kind personal --reason "restriction resolved"
/admin ban group -900001 --reason "operator investigation"
/admin requests group -900001
/admin accept group -900001 --request-id 12
/admin reject group -900001 --request-id 12
/admin approve group -900001
/admin beta-grant user 900001 --feature '*'
/admin beta-revoke user 900001 --feature '*'
```

User bans support `personal` and `full`; groups support only `full`.
Internal reasons remain private operational records. Only `--public` supplies an optional
public-safe explanation. Inspection excludes internal reasons, encryption material, and message contents.
Specific beta grants use a registered beta feature key; group grants require a specific feature.
Eligibility never activates a feature or grants ordinary group approval.
Approval does not remove an independent group ban or bypass processing restrictions.
Limit controls, emergency actions, broadcasts, and recovery remain separate later workflows.

The offline `hovorun-admin access` topic accepts the same action, target kind, target reference
and options as private `/admin`. It calls the same application services with CLI audit origin.
It requires database/host access, needs no Telegram authentication, Valkey, bot token or
message-encryption credentials, and works before the first bot administrator exists.
It performs no network lookup or membership fabrication. Existing role and trust topics remain supported.

```sh
uv run hovorun-admin access inspect user 900001
uv run hovorun-admin access inspect group -900001
uv run hovorun-admin access trust user @known_user
uv run hovorun-admin access ban user 900001 --reason 'operator investigation'
uv run hovorun-admin access unban user 900001 --reason 'restriction resolved'
uv run hovorun-admin access requests group -900001
uv run hovorun-admin access accept group -900001 --request-id 12
uv run hovorun-admin access approve group -900001
uv run hovorun-admin access beta-grant user 900001 --feature '*'
```

Access commands print structured JSON containing operational state or mutation results.
Unknown/wrong-kind/deleted targets, unknown feature keys and stale request decisions fail
with a nonzero exit status. Failed validation never prints submitted internal reasons.
Inspect output excludes internal restriction notes, keys, salts and private payloads.
CLI retains the existing protected-role safeguards and can manage protected personal restrictions
where Telegram administration cannot. No new command changes authority roles indirectly.

See [PostgreSQL setup and one-time SQLite transfer](postgresql-migration.md) for credentials, migration order and backup preservation.
