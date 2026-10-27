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
container is already running. Live administrator and owner checks use these database roles through Casbin. No owner/admin IDs are imported from
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

Membership evidence expires after five minutes. Startup and the worker refresh up to ten due groups per minute, with up to fifty relevant member checks per group and a thirty-second cycle deadline. Telegram requests have ten-second deadlines. Unprocessed evidence remains expired; runtime full-ban checks cover all retained administrator candidates. Ordinary messages do not trigger administrator-list API calls.

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
so a later local activation records supporting administrators again. Menus and delivery integration remain separate work.

## Feature selections

Feature selection is explicit: disabling every media feature keeps capture disabled, including after repeated approval or restart. Existing migrated empty selections retain their documented compatibility until an intentional feature setting edit. Private and group configuration follows the same selection rules. Provider toggles control their video/post children; enabling one child does not enable the other.

## Group restrictions

Application bans and rejected access requests do not make the bot leave a group. Join, rejoin and maintenance preserve restrictions and approvals. Restricted groups cannot process features, even for bot administrators. A denied group message may receive fixed guidance to contact an administrator privately, limited to one attempt per group and restriction reason every 60 seconds. Internal ban reasons never appear in that guidance.
