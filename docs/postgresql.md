# PostgreSQL setup

Live bot and administration persistence uses PostgreSQL through SQLAlchemy and psycopg.
Valkey remains the cache and broker server.

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
Valkey, the migration tool, the bot, media/browser/operations workers and one Beat scheduler.

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

The standalone production file requires all PostgreSQL settings, `BOT_TOKEN` and
`CACHE_ENCRYPTION_KEY`, and has no development-password fallback. Its `DATABASE_URL` must
use hostname `postgres`, the configured database/user, and the same password as
`POSTGRES_PASSWORD`. Updating these variables does not change
passwords in an already initialized PostgreSQL volume; rotate an existing database role
explicitly when needed.

PostgreSQL 18 stores data in the named volume mounted at `/var/lib/postgresql`.
Valkey has its own named volume with AOF enabled; Beat stores its schedule in a separate
named volume. Production keeps the original
`hovorunv2` project name and its existing volume prefix. Development uses the separate
`hovorunv2-dev` project, images and volumes; it cannot select production volumes by default.
Old local volumes are retained rather than copied or deleted automatically. If an existing
installation used a custom Compose project name, retain `COMPOSE_PROJECT_NAME` when operating it. Neither service stores persistent state
inside an application container. `make stop` preserves both volumes; do not use `down -v`
unless intentionally discarding their data.

## Fresh installations and independent restarts

A dedicated `migrate` container upgrades the database before the bot starts. Every service uses
a direct command and no shared shell entrypoint; migration does not rerun for administration,
worker or Beat starts. Fresh PostgreSQL installations use the preserved migration chain.
The role CLI requires PostgreSQL access but no Telegram, browser or Valkey connection.

Native media, browser and operations workers run as separate services with process-local
resources and shared PostgreSQL/Valkey addresses. Beat is a singleton service with a private
schedule volume. Media/operations/Beat use the lean image; only the browser worker contains
Chromium and DrissionPage and starts the browser lazily inside an execution child.

## Tests

Run `make dev` before `PYTHONPATH=src uv run pytest`. Tests use real PostgreSQL and local
Valkey with isolated worker storage. DDL and migration tests create fresh schemas and remove
only those schemas afterward. Set `TEST_DATABASE_URL` for a dedicated test database; its role
must have permission to create schemas.
