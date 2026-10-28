# PostgreSQL setup and one-time SQLite transfer

Live bot and administration persistence uses PostgreSQL through SQLAlchemy and psycopg.
Valkey remains the cache and broker server. SQLite support is retained only for reading
legacy databases and testing historical upgrades.

## Development and production credentials

`docker-compose.dev.yml` contains public development-only credentials: database/user/password
`hovorun`. Database ports bind to localhost. `make dev` starts PostgreSQL and Valkey with
named data volumes. The default local `DATABASE_URL` uses those credentials. `example.env` is a server template with blank database secrets, so copying it cannot silently select development passwords in production.

On the server, run `make env-init` to generate a private, mode-0600 `.env` with a random
PostgreSQL password, matching container connection URL and encryption passphrase. Set
`BOT_TOKEN` before starting the bot. The generator never overwrites an existing `.env`.
For an existing installation, preserve `BOT_TOKEN` and especially `CACHE_ENCRYPTION_KEY`;
add `POSTGRES_DB`, `POSTGRES_USER`, a server-generated `POSTGRES_PASSWORD`, and a matching
`DATABASE_URL` manually. Use a URL-safe random password, for example `secrets.token_urlsafe(32)`.
Do not commit `.env` or replace a working encryption key during database migration.

Each Compose file defines the complete stack independently. `make run-dev` builds/starts
only `docker-compose.dev.yml`; `make run` builds/starts only `docker-compose.prod.yml`.
No base file, merge or production profile is needed. Core services include PostgreSQL,
Valkey, the migration tool and the bot; `import-sqlite` is an explicitly invoked tool.

```bash
make run-dev
make stop-dev
make run
make stop
```

Generic Docker targets accept `COMPOSE_ENV=dev` or `COMPOSE_ENV=prod` (the default),
including `migrate`, `compose-config` and `compose-run`. `APP_ENV_FILE=.env.dev` selects
another private environment file consistently for interpolation and the bot container.
The `make dev` shortcut starts only development PostgreSQL/Valkey for host-side tests.

The standalone production file requires all PostgreSQL settings and has no development-password
fallback. Its `DATABASE_URL` must use hostname `postgres`, the configured database/user,
and the same password as `POSTGRES_PASSWORD`. Updating these variables does not change
passwords in an already initialized PostgreSQL volume; rotate an existing database role
explicitly when needed.

PostgreSQL 18 stores data in the named volume mounted at `/var/lib/postgresql`.
Valkey has its own named volume with AOF enabled. Production keeps the original
`hovorunv2` project name and its existing volume prefix. Development uses the separate
`hovorunv2-dev` project, images and volumes; it cannot select production volumes by default.
Old local volumes are retained rather than copied or deleted automatically. If an existing
installation used a custom Compose project name, retain `COMPOSE_PROJECT_NAME` when operating it. Neither service stores persistent state
inside an application container. `make stop` preserves both volumes; do not use `down -v`
unless intentionally discarding their data.

## Existing SQLite installation

The transfer deliberately follows the existing revision history:

1. Create a consistent read-only-source SQLite backup and upgrade that temporary dump only
   through release **0.2.1**, revision `f3a4b5c6d7e8`, verified against tag `v0.2.1`.
2. Create the same revision in an empty PostgreSQL schema, copy every table in dependency
   order, and compare full normalized row contents and counts. Preserve original IDs,
   command associations, language settings, salts and system values. Reset generated-ID sequences.
3. Apply all later migrations on PostgreSQL, including their existing data backfills, and
   append one minimal migration audit summary. Revision `postgres041` aligns PostgreSQL JSON
   and 64-bit Telegram identifier storage. Original migration filenames and revision IDs remain.

All PostgreSQL schema creation, data transfer and newer upgrades share one transaction.
An error rolls them back. The original SQLite database is never upgraded or written.
The temporary dump is private and removed afterward; retain an independent backup of the
original before cutover. Unknown tables/columns, unsupported revisions and any existing
PostgreSQL tables cause refusal rather than silent data loss or merging. A successful
transfer cannot be repeated against that target. Sources already upgraded beyond 0.2.1
need a separately reviewed transfer path; the command never downgrades them.

Stop the old bot and all SQLite writers first. Prepare server credentials and build the new
image, but **do not run the normal migration service or new bot before importing**: the target
schema must still be empty. With the old database at `data/bot.db`, run:

```bash
make compose-run ARGS="--build --volume '$(pwd)/data:/source:ro' import-sqlite /source/bot.db"
```

The importer starts PostgreSQL as its dependency and accesses no Telegram or Valkey services.
It prints table names/counts on success. Errors omit submitted values, credentials and row data.
After successful verification, start the new application with `make run`. Its migration service
observes the already-upgraded database. Keep the SQLite backup until the PostgreSQL deployment
has been reviewed. Returning to SQLite after new PostgreSQL writes requires reconciling those writes.

The equivalent local command is:

```bash
DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE' \
  uv run hovorun-import-sqlite /path/to/legacy.db
```

Prefer loading real credentials from private environment configuration rather than shell history.

## Fresh installations and independent restarts

A dedicated `migrate` container upgrades the database before the bot starts. The shared image
entrypoint only launches its command; it does not rerun migrations for every administration,
import or future worker command. Fresh PostgreSQL installations use the preserved migration chain.
The role CLI requires PostgreSQL access but no Telegram, browser or Valkey connection.

Future Celery workers will run as separate services with process-local resources and shared
PostgreSQL/Valkey addresses. Actual feature-worker startup remains pending the executor design;
this database change does not register a placeholder task or start an unfinished worker.

## Tests

Application tests use real PostgreSQL in fresh random schemas, removed after each test.
They never truncate a database or drop a pre-existing schema. Set `TEST_DATABASE_URL` to a
dedicated PostgreSQL database when the local development address is unsuitable:

```bash
make dev
PYTHONPATH=src uv run pytest
```

The test database user needs permission to create schemas. Historical migration tests continue
using temporary SQLite files; transfer tests exercise real PostgreSQL upgrades, preserved source
bytes, full-content verification, sequence repair, refusal on replay and transactional rollback.


The SQLite dependency belongs to the `legacy-import` installation extra, not live PostgreSQL persistence. Development installs include it for historical/import tests. Install tool dependencies with `uv sync --extra legacy-import`; container images currently include the tool extra until role-specific images are introduced. Standalone Alembic uses a synchronous tool connection; asynchronous callers pass a connection through `AsyncConnection.run_sync`. The importer uses its own schema-scoped exclusion key, independent of ordinary mutation locks.

Revision inspection uses Alembic MigrationContext; PostgreSQL catalog functions use SQLAlchemy expressions. Remaining raw SQL is limited to SQLite integrity PRAGMA and transactional ALTER SEQUENCE RESTART. PostgreSQL has no SQLAlchemy public schema construct for the latter; setval is not a transactional substitute. The source database and existing encryption keys remain unchanged.
