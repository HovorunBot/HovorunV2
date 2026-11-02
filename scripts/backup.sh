#!/usr/bin/env sh
# Snapshot Postgres, Valkey, and Beat state into backups/<timestamp>/, keeping the newest N.
#
# Usage: backup.sh <compose-wrapper...>
# The compose wrapper is the project's docker-stack.py invocation (e.g. the Makefile
# $(DOCKER_COMPOSE) value), so credentials and the standalone compose file stay consistent.
set -eu

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
stamp="$(date +%Y%m%d-%H%M%S)"
dest="$root/backups/$stamp"
keep="${BACKUP_KEEP:-7}"

mkdir -p "$dest"

# Postgres logical dump over localhost TCP so password auth is used without echoing the secret.
"$@" exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    > "$dest/postgres.sql"

# Valkey point-in-time RDB snapshot.
"$@" exec -T valkey valkey-cli --rdb - > "$dest/valkey.rdb"

# Beat schedule is best-effort state; copy it when the service is running.
"$@" cp beat:/var/lib/celery/beat "$dest/beat" 2>/dev/null || true

# Retain only the newest "$keep" snapshots.
find "$root/backups" -mindepth 1 -maxdepth 1 -type d | sort -r | tail -n "+$((keep + 1))" | xargs -r rm -rf

echo "Backup written to $dest"
