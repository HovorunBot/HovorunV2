# Operations rollout and recovery

[Back to README](../README.md) · [Administration](administration.md)

This guide describes the safe 0.3.0 upgrade path: back up, migrate, bootstrap administrators, start the services, verify, and recover deliberately. It is documentation only. It does not authorize a deployment, a production migration, or a Git operation; obtain explicit operator approval before running it against real infrastructure.

Use synthetic numeric IDs in examples. Never place real tokens, keys, message contents, or private profiles in tickets or logs.

## 1. Pre-flight

- Confirm the target release, the current database backup, and the current Valkey AOF snapshot.
- Keep the existing `CACHE_ENCRYPTION_KEY` unchanged. Rotating it makes encrypted message payloads and stored media references unreadable. Store the key in the secret manager before and after the rollout.
- Keep the PostgreSQL and Valkey volumes. Do not recreate them during an upgrade.
- Validate the resolved Compose configuration without starting services:

```bash
make compose-config
```

## 2. Backup before migration

PostgreSQL:

```bash
pg_dump --format=custom --file=hovorun-before-0.3.0.dump "$DATABASE_URL"
```

Valkey AOF:

```bash
redis-cli -u "$VALKEY_URL" CONFIG SET appendonly yes
redis-cli -u "$VALKEY_URL" CONFIG SET appendfsync everysec
redis-cli -u "$VALKEY_URL" BGREWRITEAOF
redis-cli -u "$VALKEY_URL" INFO persistence
```

Confirm that the configured `appendonly`/`dir` volume is the one backed up. Existing AOF files must be preserved; do not start a fresh empty Valkey next to the old volume.

## 3. Migrate

Build the selected images and run the one-shot migration service:

```bash
make update
make migrate
```

Or, against an explicitly configured database, run Alembic directly:

```bash
uv run alembic upgrade head
```

Verify a single head and the applied revision:

```bash
uv run alembic heads
uv run alembic current
```

The 0.3.0 chain ends at `chatcmd062`. A fresh isolated database can be upgraded from an empty schema to that head and downgraded back to `base` by the migration test suite.

## 4. Bootstrap authority offline

Numeric IDs can be provisioned before either operator contacts the bot. The CLI creates the identity in the same audited transaction:

```bash
uv run hovorun-admin role add 123456789 --role owner --reason "Initial bootstrap"
uv run hovorun-admin role add 123456788 --role admin --reason "Operations administrator"
uv run hovorun-admin role list
```

The example IDs are synthetic. Role changes are CLI-only. Repeating an assignment is an idempotent no-op and does not duplicate audit entries. Handles (`@name`) require an already active stored user; use numeric IDs for a fresh install.

## 5. Start

```bash
make deploy
```

This recreates changed services (`bot`, `worker-media`, `worker-operations`, `beat`, and `worker-browser` only when `BROWSER_ENABLED=True`) and keeps PostgreSQL/Valkey. Browser fallback defaults to disabled; set the switch in the selected `.env` before building to opt in. Disabling it stops the previous browser worker on application startup. Builds leverage persistent daemon BuildKit caching (`DOCKER_BUILDKIT=1`) across invocations, with `FORCE=1` available to bypass cache. Unused application images are cleaned before builds and after successful startup. Stopped containers remain available for failure diagnostics until explicit cleanup. Run `make clean` to remove older build caches and local artifacts as well; this preserves application volumes and running containers while pruning dangling images and BuildKit cache. Confirm all services are healthy and that `beat` is the single scheduler instance.

## 6. Verify

- `/start` and `/settings` respond for a provisioned operator.
- `/usage` shows current allowances; `/audit list --type user_role` shows the bootstrap assignments.
- A bounded media request reaches a terminal outcome and the worker/Beat logs contain no payloads or tokens.
- Group approvals, enabled commands, beta selections, notification preferences, and bans are still present after migration.
- `/recover user|group <synthetic id>` shows the deletion age, retained dependants, and both recovery options for a previously deleted record (without restoring it).

## 7. Group permissions and configuration preservation

Migrations are additive and preserve `group_approvals`, `group_access_requests`, `group_routes`, `chat_commands`, memberships, beta selections, notification preferences, and bans. Command removal now keeps a soft-deleted `chat_commands` row; normal joins exclude it and re-enabling revives the same row. No migration hard-deletes group configuration.

Remove stale environment overrides deliberately, one at a time, and restart only the affected service. Keep the encryption key and the active database/queue volumes.

## 8. Recovery choices

Preview first, then choose one option explicitly:

- `/recover user <id>` or `/recover group <id>` shows the retained state and the confirmation revision.
- Restore keeps prior role/trust/settings, increments the generation, and is rejected for a stale revision. Restoring ADMIN/OWNER requires the CLI (`lifecycle restore-user --revision ... --yes`).
- Fresh creates a default USER or unapproved group generation and retains the tombstone. Neither option revives cancelled queue work or Valkey message contents.
- Bans and usage aggregates are keyed by the stable numeric identity, so recovery cannot reset them.

## 9. Failure recovery

- **Migration failure:** stop services, restore the PostgreSQL backup, restore the matching AOF snapshot, and retry after reviewing the failing revision. Prefer a restore over ad-hoc downgrades.
- **Lost Valkey AOF:** admitted media jobs may be lost; the bounded recovery sweep clears stale admission state. Message contents were never stored in the audit ledger, so cancelled or lost work is not recreated.
- **Bot token or provider outage:** logs are privacy-filtered; do not paste exception bodies or URLs into tickets.
- **Worker crash:** restart the affected role. Claims are fenced; a duplicate claim cannot execute the same job twice.
- **Telegram send/AOF crash window:** a Telegram send can succeed while the worker crashes before recording the receipt, so a retry may duplicate one notification. AOF `everysec` may lose up to about one second of accepted work. Neither window is exactly-once; document duplicates rather than editing history.

## 10. Known limitations

- No hard deletion, audit purge, or automatic restore on the next message.
- No public group audit feed or mass export by untrusted users.
- Media loads are bounded; the active defaults (2 running, 500 pending, 64 KiB per payload, 32 MiB total, 300 s deadline, 120 s execution, ingress 10) remain in force.
- CLI-origin feature transitions currently persist state without publishing public outage notices; use the private `/emergency` command when a public notice is required.
