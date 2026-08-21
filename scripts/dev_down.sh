#!/usr/bin/env bash
# Stops what scripts/dev_up.sh started: the API, the Celery worker, and MinIO.
# Postgres/Redis are left running as ordinary Homebrew services (brew services
# stop postgresql@16 redis if you want them down too).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCALDEV="$ROOT/.localdev"

for name in api worker minio; do
  pidfile="$LOCALDEV/$name.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "stopped $name (pid $pid)"
    fi
    rm -f "$pidfile"
  fi
done
