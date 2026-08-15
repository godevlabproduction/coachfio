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
