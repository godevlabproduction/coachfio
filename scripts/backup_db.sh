#!/bin/sh
# Nightly Postgres dump, run inside the db-backup container (postgres:16-alpine,
# so pg_dump matches the server major version). Writes gzipped custom-format
# dumps (pg_restore can restore tables selectively) into /backups and keeps
# RETENTION_DAYS of history.
#
# Restore drill: DEPLOY.md > "Restore a backup". Run it once BEFORE launch.
set -eu

: "${POSTGRES_USER:?}"
: "${POSTGRES_PASSWORD:?}"
: "${POSTGRES_DB:=coachio}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
export PGPASSWORD="$POSTGRES_PASSWORD"

while true; do
  ts="$(date -u +%Y%m%d-%H%M%S)"
  out="/backups/${POSTGRES_DB}-${ts}.dump.gz"
  echo "backup: dumping ${POSTGRES_DB} -> ${out}"
  if pg_dump -h postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc | gzip > "$out"; then
    echo "backup: done ($(du -h "$out" | cut -f1))"
  else
    # Leave no truncated file behind - a partial dump that LOOKS like a backup
    # is worse than a loudly missing one.
    rm -f "$out"
    echo "backup: FAILED for ${ts}" >&2
  fi
  find /backups -name "*.dump.gz" -mtime "+${RETENTION_DAYS}" -delete
  # Once a day, anchored to the loop start rather than cron: no crond in the
  # container, and drift of a few minutes a day is irrelevant here.
  sleep 86400
done
