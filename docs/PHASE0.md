# Phase 0 - proving OCR reads real footage

Phase 0 answers exactly one question: **can local OCR read the FC 26 HUD (score,
clock, final stat screen) reliably enough to build on?** Everything here exists
to answer that fast, cheaply, and honestly.

## Two ways to run

### A. Full web flow (what the product will be)
```bash
cp .env.example .env
docker compose up --build           # postgres, redis, minio, api, worker
cd web && npm install && npm run dev # http://localhost:5173
```
Drop a clip → frames are extracted **in the browser** → uploaded → the worker
runs Stage 1 → raw JSON appears, and the match joins the trends table.

### B. CLI (fastest loop for calibration - no DB, no queue)
The CLI is the tool you'll actually use to tune the HUD schema. It runs the same
core code against a clip and prints JSON.

```bash
# analyse a clip
docker compose run --rm -v "$PWD:/data" api \
  python -m tools.cli analyze /data/match.mp4 --fps 1 --platform ps5 --resolution 1920x1080

# boot without OCR installed (plumbing check only)
docker compose run --rm -v "$PWD:/data" api \
  python -m tools.cli --ocr stub analyze /data/match.mp4
```

## Calibrating the HUD schema (the important part)

The coordinates in `adapters/ea_fc_26/config/hud.yaml` are **placeholders**. They
are normalized fractions of the frame, so one schema covers 1080p/1440p/4K, but
they still have to be positioned over the real HUD.

Use the overlay tool: it draws every region box on a real frame and prints what
OCR reads inside each.

```bash
# a full-time / match-facts frame (find a timestamp where it's on screen)
docker compose run --rm -v "$PWD:/data" api \
  python -m tools.cli overlay /data/match.mp4 --at 615 --scene stat_screen --out /data/overlay.png

# an in-play frame for the scoreboard/clock
docker compose run --rm -v "$PWD:/data" api \
  python -m tools.cli overlay /data/match.mp4 --at 120 --scene in_match --out /data/overlay_live.png
```

Open the PNG. For each box:
- box not over the number → adjust that region's `rect: [x, y, w, h]` in `hud.yaml`
- box right but text wrong → tighten `whitelist`, or the digits may be too small
  (raise the browser/CLI extraction resolution)

Re-run until every box sits on its value and the printed reads are correct.
`region.rect` is the only thing you touch - no code.

## How to judge "reliable enough"

Run `analyze` on several real matches and compare against what you can see:
- **Final score** must match every time (it's the backbone of every metric).
- **Stat screen detected** (`stat_screen_frames` non-empty) whenever the match
  actually shows one.
- **parse_confidence ≥ ~0.7** on good footage. The pipeline flags anything below
  0.4 as a likely stale schema / miscalibration rather than emitting garbage.

If the score and stat-screen numbers come out right across a handful of clips,
Phase 0 has passed and Phase 1 (Stages 2-3, accounts, billing) is worth building.
If they don't, we fix extraction before anything else exists - which is the whole
point of doing this first.

## When a game patch moves the HUD

`hud.yaml` carries `schema_version`. If a patch shifts the HUD, parse_confidence
drops and the match gains a warning naming the schema version. That's the signal
to grab a new frame, re-run `overlay`, nudge the rects, and bump `schema_version`
- never silently ship misread numbers.
