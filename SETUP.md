# Coach.io - Getting Started (read me first)

AI gameplay coach: upload a recorded EA FC 26 match video → Gemini watches the
whole thing → you get a personalized coaching report (mistakes, positioning,
goal-by-goal with clickable timestamps, drills). Game-agnostic core; CS2 replay
analysis already works as a second plugin.

> 🤖 **If you are Claude Code:** read `CLAUDE.md` next - it has the architecture,
> the hard-won gotchas, and the one rule that overrides everything (no game ids
> in `/core`). This file is just how to get the stack running.

## Prerequisites

- **Docker Desktop** (on Windows it needs WSL2 - the installer handles it, one
  reboot). That's it. No local Python, no ffmpeg - everything runs in containers.
- A **Google Gemini API key** (free to create): https://aistudio.google.com/app/apikey
  ⚠️ You need your OWN key - keys are never committed to this repo. Note: the
  free tier's quota is too small for full matches; enable billing (Tier 1) on
  the key's project or analysis will 429.

## Run it (first time)

```bash
git clone <this-repo>
cd coachio
cp .env.example .env
```

Now edit `.env` and set the vision engine to Gemini (the recommended block is
documented inside the file):

```
VISION_ENGINE=openai
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
OPENAI_API_KEY=<your key here>
STAGE2_MODEL=gemini-flash-latest
STAGE3_MODEL=gemini-pro-latest
ENABLE_STAGE_2=true
ENABLE_STAGE_3=true
```

Then:

```bash
docker compose up -d        # first run builds images (~a few minutes)
```

Open **http://localhost:8000** - that's the whole app (upload → analyzing →
report → moments → trends). Upload an MP4 of a match, pick Home/Away (which side
YOU played), and wait ~2-3 min.

## Daily commands

```bash
docker compose up -d                       # start everything
docker compose run --rm api pytest -q      # tests (no key needed)
docker compose restart worker api          # ALWAYS after editing core/adapter code
docker compose run --rm api python -m tools.reset_data   # wipe matches
docker compose logs -f worker              # watch an analysis run
```

**The #1 gotcha:** the Celery worker does NOT auto-reload code. If you change
anything under `core/`, `adapters/`, or `workers/`, run
`docker compose restart worker api` or you'll be running stale code and lose an
hour wondering why (ask us how we know).

**#2:** changing values in `.env` needs `docker compose up -d --force-recreate api worker`
(plain `restart` reuses the old env).

## Teach the coach (knowledge brain)

The coach grounds its advice in `adapters/ea_fc_26/knowledge/*.yaml`. Grow it:

```bash
# from a tutorial/meta video (Gemini watches it, ~$0.02):
docker compose run --rm -v "C:/path/to/videos:/dl:ro" api \
  python -m tools.learn_from_video /dl/some_tutorial.mp4

# from text notes (free) - see tools/notes_meta_v1.yaml for the format:
docker compose run --rm api python -m tools.learn_from_text tools/my_notes.yaml

# then reload so the coach uses it:
docker compose restart worker api
```

## Repo map (30 seconds)

```
core/        game-agnostic engine (Match/Event/Metric/Insight) - NEVER name a game here
adapters/    game plugins: ea_fc_26 (video+OCR), cs2 (replay JSON)
api/         FastAPI - also serves the frontend at :8000
workers/     Celery task that runs the pipeline
frontend/    static web UI (served by the API; edits are live on refresh)
tools/       CLI utilities (rerun_match, learn_from_video, export_pdf, ...)
tests/       pytest - includes the guard that fails the build if a game id leaks into core
```

## Working together

- Branch from `main`, open PRs; keep `main` deployable.
- Put a short "what changed & why" in every PR body - the other person's Claude
  reads it for context.
- Never commit `.env`, keys, or videos (`.gitignore` already blocks them).

## Subdomains: one site per game

`fifa.coachfio.com` serves the FC adapter; the bare domain serves a chooser. One
frontend is deployed for every host and asks `GET /api/site` at boot which game
it is, so **adding a game never needs a frontend change**.

The mapping is config, not code:

```
SITE_HOSTS=fifa=ea-fc@26,cs=cs2@2        # <label>=<game_id>@<edition>
SITE_ROOT_LABELS=www,app,localhost,127   # labels that mean "no game, show chooser"
```

An unmapped subdomain resolves to the chooser rather than falling back to the
only installed game. That is deliberate: silently serving FC on
`valorant.coachfio.com` would file uploads against the wrong adapter, and
nothing would look broken until someone read the report.

### Testing locally

Host-header override, no DNS needed:

```bash
curl -s -H "Host: fifa.coachfio.com" localhost:8000/api/site
```

For the browser, add to `/etc/hosts`, then visit `http://fifa.localtest.me:8000`:

```
127.0.0.1  fifa.coachfio.local coachfio.local
```

(`*.localtest.me` already resolves to 127.0.0.1 publicly, so it needs no hosts
entry, but the label must be listed in `SITE_HOSTS`.)

### Deploying

1. **DNS**: an A/AAAA record for the apex plus a wildcard `*.coachfio.com` to the
   same origin, so a new game needs no DNS change.
2. **TLS**: a wildcard certificate for `*.coachfio.com`, plus the apex. A
   per-subdomain certificate means issuing one for every new game.
3. **Proxy**: pass the original `Host` header through (`proxy_set_header Host
   $host` in nginx). Resolution reads it, so a proxy that rewrites Host makes
   every site resolve to the chooser.

### Sessions across subdomains

`localStorage` is per-origin, so the current dev identity does **not** carry from
`coachfio.com` to `fifa.coachfio.com`: you appear signed out on each. Accounts,
coach links and chat are game-agnostic (no game column on those tables), so they
must be shared across every site. The fix is the hosted auth provider at the
`current_user` seam issuing a cookie scoped to `.coachfio.com`. Until that lands,
treat each subdomain as a separate sign-in.
