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

Groups need approval and fresh, successful administrator verification. Missing, failed, future or expired evidence pauses processing without revoking approval. Group bans and full bans on current Telegram administrators suspend group processing for everyone. A ban on an ordinary member does not suspend unrelated members. Private bot administration remains available to authorized bot administrators, subject to any personal restriction applied through CLI.

Membership evidence expires after five minutes. Startup and the worker refresh up to ten due groups per minute, with up to fifty relevant member checks per group and a thirty-second cycle deadline. Telegram requests have ten-second deadlines. Unprocessed evidence remains expired; runtime full-ban checks cover all retained administrator candidates. Ordinary messages do not trigger administrator-list API calls.
