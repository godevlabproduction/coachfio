# CLAUDE.md - working notes for this repo

> New to the repo? `SETUP.md` has the run-it-first steps. This file is the
> architecture + gotchas. The **current main path** is `SourceType.VIDEO_NATIVE`:
> the web UI at :8000 uploads a whole match video → Gemini watches it natively
> (`core/ai/gemini_video.py`, `GeminiVideoCoaching` in `core/pipeline/stages.py`)
> → coaching report with clickable timestamps. Default is **middle mode** (one
> coaching call + a small deterministic scoreboard read for the exact score and
> timed goal-by-goal, ~$0.02-0.05/match); `GEMINI_VIDEO_TWO_PASS=true` enables
> the deep multi-pass mode. The coach grounds itself in a growing knowledge
> brain (`adapters/ea_fc_26/knowledge/*.yaml`) - feed it with
> `tools/learn_from_video.py` (Gemini watches a tutorial clip) or
> `tools/learn_from_text.py` (distilled notes). The static frontend in
> `frontend/` is served BY the API at :8000 (same origin; edits live on refresh,
> but browsers cache `coach.js` - hard-refresh). The frame-OCR pipeline below
> still exists and CS2 still proves the game-agnostic core.

Coach.io: a **game-agnostic** gameplay-analysis engine. FC 26 is the first
plugin, not the product. Read the README for the full picture; this file is the
fast path for an agent picking up work.

## The one rule that overrides everything

The core knows only **Match / Event / Metric / Insight**. Games are plugins in
`/adapters`. **Never write `if game == "fc26"` (or any game id) in `/core`.**
There's a test that fails the build if a game id leaks into `/core`
(`tests/test_core.py::test_no_game_branching_in_core`). If you feel the urge to
special-case a game in core, the design is wrong - push it into the adapter.

**Source types are abstracted too.** The pipeline dispatches on `SourceType`
(a core enum), not the game: `VIDEO` → ffmpeg frames + OCR; `REPLAY`/`API` →
`adapter.ingest(bytes)` parses structured data directly. Phase 2 proved this by
adding **CS2** (a replay/stats source, no video) as a pure `/adapters/cs2`
plugin - see `adapters/cs2/` and `tests/test_cs2.py`.

## Architecture (cost-ordered pipeline)

- **Stage 1 - local, $0** (the only live stage): ffmpeg frames → scene diff →
  PaddleOCR reads the HUD via the adapter's data-driven HUD schema →
  `adapter.interpret()` produces Match/Event/Metric. This is all Phase 0.
- **Stage 2 / 3 - Phase 1, stubbed + gated off**: small then large vision model
  over only the candidate/important frames. Recommended models: Haiku 4.5
  (Stage 2) and Sonnet 5 / Opus 5 (Stage 3). Hard budget **$0.25 / 15-min
  match**, enforced by `core/pipeline/cost.py` (fails loudly).

Adapters are **~90% declarative**: `adapters/ea_fc_26/config/*.yaml` (identity,
HUD regions, metrics, vocab) + a small `adapter.py` (`interpret()`). HUD coords
are **normalized** [0-1] so one schema scales across resolutions.

## Dev environment gotchas (these bit us - don't relearn them)

- **No local Python/ffmpeg.** Run everything in Docker (`docker compose run --rm
  api ...`). A portable ffmpeg for frame inspection lives in the session
  scratchpad, not on PATH.
- **PowerShell is the shell.** Inline `python -c "..."` with quotes gets mangled
  - write a `tools/*.py` script and run it as a module instead.
- **Docker Desktop needs WSL2** (admin + reboot to install - user action).
- **PaddleOCR must stay pinned to 2.x / numpy-1.x** (`pyproject.toml [ocr]`).
  3.x hits a CPU oneDNN bug and numpy-2 breaks the OpenCV/Paddle ABI. See the
  pinning comments there before bumping anything.
- **The Celery worker does NOT auto-reload.** After editing adapter/core code,
  `docker compose restart worker` or the web path runs stale code. The CLI
  (`docker compose run api python -m tools.cli`) always uses fresh code (volume
  mount), so use it to iterate on extraction logic.
- **`docker compose exec postgres psql ... TRUNCATE` can hang** on a table lock;
  use `python -m tools.reset_data` (weaker lock + timeout) instead.

## OCR robustness - the hard-won design

Real footage is full of replays/cutscenes that inject **confident phantom
digits** into the fixed HUD region (we saw `home='94' away='8'` at 1.00 conf for
a 6-frame corner kick, and a crest merging with a digit). `interpret()` defends
in layers - do not remove these without understanding why:
1. Plausibility cap (score ≤ 19).
2. Live-frame gating (only trust frames whose clock parses as MM:SS).
3. **Final score = trailing-window majority** (end-of-match consensus), NOT
   `max()` - a transient phantom that reverts never reaches the end.
4. Events capped at the final score.
Regression-tested in `tests/test_scoring.py` - keep those green.

## Commands

```bash
docker compose up -d                              # bring up the stack
docker compose run --rm api pytest -q             # tests (no OCR/ffmpeg/DB)
docker compose run --rm -v "$PWD/clips:/data:ro" api \
  python -m tools.cli --resolution 1280x720 analyze /data/x.mp4 --fps 1
docker compose run --rm api python -m tools.reset_data   # clear matches
docker compose restart worker                     # after editing core/adapter code
cd web && npm run dev                             # web UI on :5173
```

## Stages 2 & 3 (Phase 1) - built, gated off by default

- Code: `core/ai/` (VisionModel: `anthropic` | `stub`, pricing, cost estimation)
  and `core/pipeline/stages.py` (`Stage2CheapEvents`, `Stage3DeepRead`).
- **Off unless** `ENABLE_STAGE_2` / `ENABLE_STAGE_3` are true. Engines
  (`core/ai/vision.py`): `anthropic` (cloud), `ollama` (local, $0), `stub` (no-op).
  Local/stub set `free=True` so the stages skip the budget estimate ($0 can't be
  halted by the $-cap). Default `stub`.
- **Fully local - configured and working on this laptop.** `.env` runs both
  stages on host Ollama with `gemma3:4b` (a 4B multimodal model), engine
  `ollama`, reached from the worker container at
  `http://host.docker.internal:11434` (Ollama already listens on `0.0.0.0`).
  Proven end-to-end: real frame → `gemma3:4b` → valid JSON label, $0.
  - ⚠️ **GPU is disabled (`OLLAMA_NUM_GPU=0`, CPU inference).** The GTX 1050 Ti's
    driver (31.0.15.5152) is too old for this Ollama build's CUDA-12 PTX - GPU
    load crashes with *"CUDA error: the provided PTX was compiled with an
    unsupported toolchain."* **Update the NVIDIA driver** (Pascal is still
    supported by current R5xx drivers) to re-enable the GPU, then set
    `OLLAMA_NUM_GPU=-1`. CPU works now, just slower (~seconds/frame).
  - Small local VLMs give **coarse** labels (a goal celebration may read as
    `in_play`). Plumbing is solid; accuracy improves with a bigger model (needs
    the GPU) or few-shot prompt tuning.
- Models: Stage 2 = `claude-haiku-4-5` (classify candidate frames), Stage 3 =
  `claude-sonnet-5` (deep-read important moments). Every call is **budget-checked
  before it runs** (estimate vs `cost.remaining`) and charged actual token cost
  after - a match can't silently overspend the $0.25 cap.
- Kept game-agnostic: Stage 2's label enum + Stage 3's insight schema come from
  the adapter (`stage2_label_schema()`, `insight_schema()`, `event_type_map()`);
  "important moment" selection uses core categories, not game words.
- Tested with a scripted offline model in `tests/test_stages.py` (event
  mapping, exclusions, budget halt) - no API key needed for tests.

## Phase 2 (built) - the abstraction, proven

Added **CS2** as a *deliberately different* second game: a **replay/stats
source, no video**. It's a pure plugin - `adapters/cs2/` (`game.yaml` + a small
`adapter.ingest()` that parses a replay JSON into Match/Event/Metric); no
`hud.yaml`, no OCR. The only core change was **general** (dispatch on
`SourceType`, not the game). Non-video upload path: `POST
/api/matches/{id}/source` → worker loads it as `source_bytes` → `Stage 1`
dispatches to `adapter.ingest()`. Validated end-to-end via API (13-11, rounds +
kills + round/highlight events, $0) and in `tests/test_cs2.py`. The
no-game-branching guard still passes → the design holds with two very different
games.

## Phase 1 extras (built)

- **Auto-clip highlights** (`HighlightClips` stage, `core/pipeline/stages.py`):
  for each important event, assembles a short H.264 mp4 from the frames around it
  (local ffmpeg, $0), stores it in object storage, records the key on
  `event.payload.clip`. Gated by `ENABLE_HIGHLIGHTS` + an object store in the
  context. Served by `GET /api/matches/{id}/clip?key=...` (path-checked).
- **Stage 3 grounding:** Stage 3 prepends OCR ground-truth (score, event time,
  team) and forbids inventing names - cuts small-model confabulation.
- **Usage limits** (`core/storage/usage.py`, `api/routes/usage.py`): matches
  analysed per identity, `FREE_MATCH_LIMIT` (402 when exceeded). Identity comes
  from `api/deps.current_user` - the seam where a hosted auth provider plugs in
  (do NOT roll your own auth). `GET /api/usage` reports used/limit/remaining.
- **Frame serving:** `GET /api/matches/{id}/frame?key=...` (path-checked) so the
  web UI can show the frames behind events/insights.
- **Web panel:** events table (with inline clips), insights (summary + coaching
  point + frames), usage counter. Still intentionally plain.

## Status

Phase 0 **done and validated** (console FC 26, full 18-min match → correct 1-1,
$0). Phase 1 Stages 2/3 **built, and validated end-to-end fully local** on this
laptop: `tools.cli analyze --full` on the cut ran OCR + local `gemma3:4b`
(CPU) → correct 1-1, a Stage-2 **substitution** event, and two Stage-3
tactical `goal_analysis` insights with coaching points - all at **$0**. Caveat:
the 4B model confabulates specifics (player/team names); structure + coaching
value are sound, precise grounding needs a bigger model (GPU) or prompt work.
Phase 2 **proven** (CS2 replay source runs through the same core). Still open:
a clip with a full-time **stats screen** to calibrate possession/shots
(regions are placeholders); real hosted auth behind the `current_user` seam;
accounts/billing. The FC-Pro **broadcast** overlay is a harder secondary variant
(`fcpro_broadcast`) - the console HUD is the real product input.
