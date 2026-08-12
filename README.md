# Coach.io — Multi-Game Gameplay Analysis Platform

A game-agnostic analysis engine. A player records gameplay, drops the video in
the browser, and gets an analysis + improvement tracking back. **FC 26 is the
first plugin, not the product.** The core knows nothing about football.

> **Design rule:** if core code ever needs `if game == "fc26"`, the design has
> failed. Games are plugins (`/adapters/*`); the core only knows about
> **Match / Event / Metric / Insight**.

## Current status: Phase 0 — the core question is answered ✅

**Can we read the HUD from real gameplay footage reliably?** On real PS5
console footage (720p+), **yes**: PaddleOCR read a full 18-minute match's
scoreboard correctly end-to-end — final **1–1 at 0.98 confidence across 1119
frames / 308 scene changes**, at **$0.00** (no AI). Validated both via the CLI
and through the full async web path (browser frame-extraction → API → MinIO →
Celery worker → Postgres → dashboard).

Key lessons that shaped the code (see `docs/CALIBRATION_FINDINGS.md`):
- **≥720p required.** 360p is below the legibility floor — reject at upload.
- **A score is the end-of-match consensus, not the max ever read.** Replays and
  set-pieces inject confident phantom digits into the fixed HUD region;
  `interpret()` uses a trailing-window majority + plausibility cap + live-clock
  gating to reject them. Regression-tested in `tests/test_scoring.py`.
- Console HUD is the product target; the FC-Pro esports *broadcast* overlay is a
  harder, secondary `fcpro_broadcast` variant.

Phase 0 scope (this build):
- One game (FC 26), video upload only, **Stage 1 extraction only — no AI cost**
- Frames extracted **in the browser** (video never uploaded whole), downscaled,
  uploaded as JPEGs
- Backend: stat-screen detection + OCR → stats as JSON
- One deliberately ugly page: upload → JSON back, plus a bare trends list
- Full async infra stood up (Postgres / Redis / MinIO / Celery) so Phase 1 is
  additive, not a rewrite

Not in Phase 0: auth, billing, charts.

**Phase 1 (in progress):** the Stage 2 (small model, event labelling) and Stage 3
(larger model, tactical insight) vision passes are **built and tested behind
flags** — see `core/ai/` and `core/pipeline/stages.py`. They're off by default
(`ENABLE_STAGE_2` / `ENABLE_STAGE_3`); set `VISION_ENGINE=anthropic` +
`ANTHROPIC_API_KEY` to run them for real. Every model call is budget-checked
before it runs and charged actual token cost after, so the **$0.25/match cap**
holds. Still to do: measure on real footage with a key.

**Phase 1 extras (built):** auto-clipped highlight moments (local ffmpeg, served
by `GET /api/matches/{id}/clip`), Stage-3 grounding to cut confabulation, and
usage limits by matches-analysed (`GET /api/usage`, HTTP 402 over the cap) with a
pluggable auth seam (`api/deps.current_user`).

**Phase 2 (proven):** the architecture test — a *deliberately different* second
game, **CS2**, added as a **replay/stats source (no video)** in `adapters/cs2/`.
It runs through the *same* `run_pipeline` (which dispatches on `SourceType`, not
the game), end-to-end via `POST /api/matches/{id}/source`. Adding it touched only
`/adapters` — the game-agnostic core held with two very different games.

## Layout (matches the design brief)

```
/core
  /models        Match, Event, Metric, Insight — game-agnostic
  /extraction    scene diff, stat-screen detection, PaddleOCR HUD reader
  /pipeline      stage orchestration, cost accounting, budget enforcement
  /storage       Postgres (SQLAlchemy) + object store (MinIO/S3)
  /progress      trend calculation — game-agnostic
/adapters
  /base          the interface every game implements + registry
  /ea_fc_26      FC 26: ~90% declarative config, ~10% code
/api             FastAPI: create match, upload frames, results, SSE progress
/workers         Celery pipeline job + Redis progress pub/sub
/web             React + TS: dropzone, client-side frame extraction, results
```

`/core`, `/adapters`, `/api`, `/workers` are sibling Python packages installed
by the root `pyproject.toml`. `/web` is a separate Node app.

## Running it

Prereqs: **Docker Desktop** (runs the whole backend) and **Node 18+** (web dev
server). You do **not** need local Python or ffmpeg — frame extraction is
client-side, and the backend runs in containers.

```bash
cp .env.example .env
docker compose up --build          # postgres, redis, minio, api, worker
cd web && npm install && npm run dev
```

Then open the web dev URL, drop a FC 26 clip, and watch the JSON come back.

See `docs/PHASE0.md` for how HUD calibration works and how to validate OCR
accuracy against a real clip.

### Useful commands

```bash
# Run the test suite (pure logic — no OCR/ffmpeg/DB needed)
docker compose run --rm api pytest -q

# Analyse a clip from the CLI, no web app (mount the folder it's in)
docker compose run --rm -v "$PWD/clips:/data:ro" api \
  python -m tools.cli --resolution 1280x720 analyze /data/match.mp4 --fps 1

# Same, but run the WHOLE pipeline incl. Stage 2/3 with the configured engine
# (e.g. local Ollama) — events + insights, no DB/upload needed:
docker compose run --rm -v "$PWD/clips:/data:ro" api \
  python -m tools.cli --resolution 1280x720 analyze /data/match.mp4 --fps 1 --full

# Draw the HUD region boxes on a frame to calibrate a new layout/game
docker compose run --rm -v "$PWD/clips:/data:ro" api \
  python -m tools.cli overlay /data/match.mp4 --at 60 --scene in_match --out /data/overlay.png

# Wipe analysed matches (clears the trends view; keeps the schema)
docker compose run --rm api python -m tools.reset_data
```
