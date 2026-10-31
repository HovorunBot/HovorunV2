# Upgrade production from 0.2.1 to 0.3.0

Use this guide to transfer an existing 0.2.1 SQLite installation to the 0.3.0
PostgreSQL runtime. The importer preserves existing records, verifies the copied
data, and applies the later database migrations.

These steps assume the standard Linux Docker installation, `.env`, and
`data/bot.db`. Run commands from the project directory in one shell session.
Adjust paths if your installation differs. If you used a custom Compose project
name, retain the same `COMPOSE_PROJECT_NAME` throughout to preserve volume names.
Docker Compose, Git, and Make must already be available on the server.

Enter your installation directory before starting. Replace the example path
with the actual repository path on your server:

```bash
cd /path/to/HovorunV2
```

Run each command in order and continue only when it succeeds. Commands in this
guide are for the production server; they do not execute a migration merely by
being added to the repository.

## 1. Stop the old installation

Run this while the 0.2.1 checkout and its `docker-compose.yml` still exist:

```bash
docker compose --profile prod stop
```

Stop any separately managed bot or worker processes too. Nothing may write to
SQLite during backup and transfer.

## 2. Back up the old installation

Create a private backup outside the repository:

```bash
umask 077
migration_backup="$HOME/hovorun-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$migration_backup"
cp -p .env "$migration_backup/"
cp -a data "$migration_backup/"
git rev-parse HEAD > "$migration_backup/previous-commit.txt"
```

For the standard 0.2.1 Valkey container, copy its stopped data directory too:

```bash
docker cp hovorun-valkey:/data "$migration_backup/valkey-data"
```

Confirm the copies exist before continuing. Keep backups private: they contain
credentials and bot data. Preserve the existing Docker volumes. Do not use
`docker compose down -v` during this upgrade.

## 3. Switch to release 0.3.0

Check for local changes first:

```bash
git status --short
```

Resolve or preserve local changes before proceeding. Once the `v0.3.0` release
tag has been published, fetch and select it:

```bash
git fetch origin --tags
git checkout v0.3.0
```

If the tag is unavailable, stop and wait for the published release. Do not
substitute an arbitrary development branch for this production procedure.

## 4. Update the existing environment file

Open `.env` in your server editor:

```bash
nano .env
```

Preserve the existing `BOT_TOKEN` and **`CACHE_ENCRYPTION_KEY` exactly**. Keep
other required provider credentials. Do not replace this file with a fresh
template or generate a new encryption key.

Add the following settings, replacing both password placeholders privately:

```dotenv
POSTGRES_DB=hovorun
POSTGRES_USER=hovorun
POSTGRES_PASSWORD=REPLACE_WITH_A_NEW_RANDOM_PASSWORD
DATABASE_URL=postgresql+psycopg://hovorun:REPLACE_WITH_THE_SAME_PASSWORD@postgres:5432/hovorun
```

Use a strong, URL-safe random password; both password values must match.
`postgres` is the Docker service hostname. The PostgreSQL target schema must be
empty. Changing these variables does not change credentials in an already
initialized PostgreSQL volume.

In nano, save with **Ctrl+O**, press **Enter**, then exit with **Ctrl+X**.

The old `DB_PATH` setting no longer selects the runtime database. Owner and
administrator authority is assigned through the CLI in step 6, rather than
through the old `OWNERS` or `ADMIN_IDS` environment lists.

Protect the file and validate configuration without starting services:

```bash
chmod 600 .env
make compose-config
```

## 5. Import SQLite into PostgreSQL

The migration tool is already implemented in
[`sqlite_to_postgres.py`](../src/hovorunv2/bootstrap/sqlite_to_postgres.py).
Run it through the `hovorun-import-sqlite` command provided by the dedicated
`import-sqlite` Docker service. You do not need to install Python or migration
dependencies on the production host.

**Do not run `make migrate`, `make run`, or `make deploy` before importing.**
Those commands can create PostgreSQL tables, preventing the importer from using
the target schema.

Confirm the original database exists. Continue only if this command succeeds:

```bash
ls -lh data/bot.db
```

Build the images first:

```bash
make build
```

After the build succeeds, copy and run this entire line:

```bash
make compose-run ARGS="--build --volume '$(pwd)/data:/source:ro' import-sqlite uv run --no-sync hovorun-import-sqlite /source/bot.db"
```

Immediately check the command's exit status, before running another command:

```bash
echo $?
```

`0` means the command succeeded. Any other value means failure: stop here and
do not start the application. `make` may report its own nonzero status rather
than the importer's original exit code.

In this command:

- `--build` builds the importer image, including its legacy SQLite dependencies.
- `--volume '$(pwd)/data:/source:ro'` mounts the host database directory read-only
  at `/source` inside the container.
- `import-sqlite` selects the one-shot Compose service.
- `uv run --no-sync hovorun-import-sqlite` runs the implemented Python importer.
- `/source/bot.db` is the source database path inside the container, not its host
  path. This is the importer's only positional argument.

If your database lives elsewhere, adjust both the host directory and the
container filename. For example, for `/srv/hovorun-data/legacy.db`:

```bash
make compose-run ARGS="--build --volume /srv/hovorun-data:/source:ro import-sqlite uv run --no-sync hovorun-import-sqlite /source/legacy.db"
```

The target connection comes from `DATABASE_URL` in `.env`, passed into the
container by Compose. Do not put credentials in the command. The source
database path is supplied explicitly; the old `DB_PATH` setting is not used.

The importer starts PostgreSQL as a dependency. It copies and verifies existing
records and applies the remaining migrations in one PostgreSQL transaction.
It does not start the bot, contact Telegram, or modify the original SQLite file.
Successful output is a JSON object with an `imported` mapping of table names
to row counts and `"upgraded_to": "head"`. A successful command exits with
status zero. No separate `make migrate` step is needed after this import.

On failure, the command exits with a nonzero status and reports that the source
was preserved and the PostgreSQL transaction rolled back. Error output is
deliberately generic to avoid exposing credentials or row data; it does not
identify the underlying failure in detail.

Continue only after successful completion. Do not repeat a successful import
against the same target. If it fails, check the source path, database settings,
source revision, and whether the target already contains tables before retrying.
Do not delete tables or downgrade the source database. Sources already upgraded
beyond 0.2.1 require a separately reviewed transfer path.

## 6. Provision your owner role

Replace `YOUR_TELEGRAM_ID` with your numeric Telegram user ID:

```bash
make compose-run ARGS="--no-deps bot uv run --no-sync hovorun-admin role add YOUR_TELEGRAM_ID --role owner"
```

For each additional bot administrator, replace `ADMIN_TELEGRAM_ID`:

```bash
make compose-run ARGS="--no-deps bot uv run --no-sync hovorun-admin role add ADMIN_TELEGRAM_ID --role admin"
```

Verify the assignments:

```bash
make compose-run ARGS="--no-deps bot uv run --no-sync hovorun-admin role list"
```

These CLI operations need PostgreSQL but do not start bot polling. Numeric IDs
can be provisioned before the operator contacts the bot.

## 7. Start 0.3.0

```bash
make deploy
```

After startup succeeds, inspect all services, including the exited migration
container:

```bash
docker compose --env-file .env -f docker-compose.prod.yml ps --all
```

The migration service should exit successfully. The bot, media worker, browser
worker, operations worker, Beat scheduler, PostgreSQL, and Valkey should remain
running. Keep exactly one Beat scheduler instance. The importer container is
temporary and is removed after its command completes.

## 8. Verify through Telegram

- Open `/start` and `/settings` privately as the provisioned owner.
- Check existing group approvals, language settings, and enabled media features.
- Send a supported media link in an approved group and confirm delivery.
- Confirm a current group administrator can manage group settings privately.

Keep the original SQLite file, the backup, and the original encryption key until
the deployment has been reviewed. If migration fails, leave the new application
stopped and investigate the reported failure before retrying. Once 0.3.0 accepts
new writes, returning to the SQLite backup requires reconciling those newer
writes; starting 0.2.1 against the old file alone does not preserve them.

See [PostgreSQL setup and SQLite transfer](postgresql-migration.md) for importer
contracts and [operations rollout](operations-rollout.md) for ongoing PostgreSQL
backup and recovery procedures.
