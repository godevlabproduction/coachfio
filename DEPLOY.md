# Deploying coachfio.com

The production stack is `docker-compose.prod.yml`: Caddy (TLS + reverse proxy)
in front of the API, worker, Postgres, Redis and MinIO - everything internal
except ports 80/443. One box runs it all. Work through this top to bottom; a
fresh launch is roughly an afternoon, most of it waiting on DNS.

## 1. Server

- Any Linux VPS with Docker + docker compose v2. Sizing to start: **4 vCPU /
  8 GB RAM / 160 GB disk** (video compression is CPU-hungry; disk holds
  Postgres + the compressed match videos). One size up is cheap insurance.
- Open ports 80 + 443 only. SSH via keys; disable password login.

```bash
git clone https://github.com/godevlabproduction/coachfio.git
cd coachfio
```

## 2. DNS

At your DNS provider, point at the server's IP:

| record | host | value |
|---|---|---|
| A | `coachfio.com` | server IP |
| A | `www.coachfio.com` | server IP |
| A | `fifa.coachfio.com` | server IP |
| A | `cs.coachfio.com` | server IP |

(Or one wildcard `*.coachfio.com` A record - but the Caddyfile issues per-host
certificates, so each served hostname still has to be listed there. Wildcard
TLS needs the DNS-01 challenge - note in `deploy/Caddyfile`.)

## 3. Configuration

```bash
cp .env.prod.example .env.prod
```

Fill in every empty value - the file says how to generate each. The three that
MUST NOT be dev values: `POSTGRES_PASSWORD`, `S3_SECRET_KEY`, `SECRET_KEY`
(the API refuses to boot with the dev SECRET_KEY while cookies are Secure).

## 4. Supabase (one-time dashboard work)

In the [Supabase dashboard](https://supabase.com/dashboard) for the project:

1. **Auth → URL Configuration**: Site URL `https://coachfio.com`; add redirect
   URLs `https://coachfio.com/auth/callback`,
   `https://fifa.coachfio.com/auth/callback`,
   `https://cs.coachfio.com/auth/callback` (one per game site - each origin
   handles its own callback).
2. **Auth → SMTP**: configure custom SMTP (Resend/Postmark/SES all work).
   **This is a launch blocker**: the built-in sender allows only a handful of
   emails per hour for the whole project - at any real traffic, sign-in dies.
3. Optional: enable Google OAuth (Discord already works). The frontend's
   `/api/auth/methods` picks it up automatically.

## 5. First boot

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

First run builds images (minutes) and Caddy fetches certificates (seconds,
needs DNS already propagated). The API runs migrations itself on startup
(Alembic; pre-Alembic databases are stamped automatically).

## 6. Smoke test (every deploy, 2 minutes)

```bash
curl -fsS https://coachfio.com/health              # {"ok":true,...} - DB is up
curl -fsS https://coachfio.com/api/site | head -c 200      # hub resolves
curl -fsS https://fifa.coachfio.com/api/site | head -c 200 # game resolves (host passthrough works)
curl -fsS https://coachfio.com/api/auth/methods    # magic_link true, oauth listed
```

Then the human test: sign in via magic link, upload a short clip, watch the
analysis complete, delete the match.

## 7. Backups - RUN THE RESTORE DRILL BEFORE LAUNCH

`db-backup` dumps Postgres nightly into the `backups` volume (14-day
retention). That survives bad deploys and fat-fingered deletes, **not a dead
disk** - sync offsite too, e.g. from the host's crontab:

```bash
# offsite sync (any S3-compatible target; Cloudflare R2 has a free tier)
docker run --rm -v coachfio_backups:/backups:ro -v ~/.mc:/root/.mc \
  minio/mc mirror /backups r2/coachfio-db-backups
```

Restore drill (do this once now, and after any Postgres major upgrade):

```bash
# list backups
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  exec db-backup ls -lh /backups
# restore INTO A SCRATCH DB and check row counts - never straight over prod
docker compose -f docker-compose.prod.yml --env-file .env.prod exec db-backup sh -c '
  export PGPASSWORD="$POSTGRES_PASSWORD" &&
  psql -h postgres -U "$POSTGRES_USER" -c "DROP DATABASE IF EXISTS restore_test" &&
  psql -h postgres -U "$POSTGRES_USER" -c "CREATE DATABASE restore_test" &&
  gunzip -c /backups/<pick-one>.dump.gz | pg_restore -h postgres -U "$POSTGRES_USER" -d restore_test &&
  psql -h postgres -U "$POSTGRES_USER" -d restore_test -c "SELECT count(*) FROM matches"'
```

## 8. Deploying updates

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
# then the smoke test above
```

Rollback = `git checkout <last-good-tag>` + the same up command. Changed
`.env.prod` values need `up -d --force-recreate api worker` (restart reuses
the old environment - same gotcha as dev).

## Gotchas already learned (don't relearn)

- **Celery does not auto-reload** - `up -d --build` recreates it; never `restart`
  after a code change.
- **Host header must pass through** the proxy untouched, or every subdomain
  resolves to the chooser. The provided Caddyfile is already correct; be
  suspicious of any proxy config change that touches `Host`.
- **Rate limiting needs the real client IP**: Caddy sets X-Forwarded-For and
  uvicorn runs `--proxy-headers`. If sign-in starts 429ing everyone at once,
  something in that chain broke and every visitor is sharing one IP bucket.
- **`SESSION_COOKIE_DOMAIN=.coachfio.com`** is what makes one sign-in cover the
  hub and every game subdomain. Leave it empty and every subdomain looks
  signed-out.
- Match videos are replaced by their 720p re-encode after successful analysis
  (`STORE_COMPRESSED_SOURCE`) - disk grows by ~100-200 MB per analysed match,
  not gigabytes.
