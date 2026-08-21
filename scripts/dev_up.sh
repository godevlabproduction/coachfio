#!/usr/bin/env bash
# Native (no-Docker) dev stack: Postgres + Redis + MinIO via Homebrew, and the
# API + Celery worker as plain background processes. Counterpart to
# `docker compose up -d` for machines without Docker Desktop. See
# scripts/dev_down.sh to stop what this starts, and CLAUDE.md/SETUP.md for the
# full picture.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCALDEV="$ROOT/.localdev"
mkdir -p "$LOCALDEV"

export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"

echo "==> Postgres + Redis (brew services)"
brew services start postgresql@16 >/dev/null
brew services start redis >/dev/null

echo "==> MinIO"
if curl -sf http://localhost:9000/minio/health/live >/dev/null 2>&1; then
  echo "    already running"
else
  mkdir -p "$HOME/coachio-minio-data"
  nohup env MINIO_ROOT_USER=coachio MINIO_ROOT_PASSWORD=coachio-secret \
    /opt/homebrew/opt/minio/bin/minio server --address ":9000" --console-address ":9001" \
    "$HOME/coachio-minio-data" > "$LOCALDEV/minio.log" 2>&1 &
  disown
  echo $! > "$LOCALDEV/minio.pid"
  for _ in $(seq 1 20); do
    curl -sf http://localhost:9000/minio/health/live >/dev/null 2>&1 && break
    sleep 0.5
  done
fi

echo "==> Waiting for Postgres"
for _ in $(seq 1 20); do
  pg_isready -q && break
  sleep 0.5
done

cd "$ROOT"
source .venv/bin/activate

echo "==> API (uvicorn --reload) -> .localdev/api.log"
nohup uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload > "$LOCALDEV/api.log" 2>&1 &
disown
echo $! > "$LOCALDEV/api.pid"

echo "==> Celery worker -> .localdev/worker.log"
nohup celery -A workers.celery_app worker --loglevel=info --concurrency=2 > "$LOCALDEV/worker.log" 2>&1 &
disown
echo $! > "$LOCALDEV/worker.pid"

sleep 1
echo
echo "Stack is up: http://localhost:8000"
echo "Logs: tail -f .localdev/api.log .localdev/worker.log .localdev/minio.log"
echo "Stop the API/worker/MinIO with: scripts/dev_down.sh"
