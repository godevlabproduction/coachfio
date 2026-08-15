# Calibration findings - first real clip

**Clip:** `videoplayback.mp4` - 640×360, 30:24, ~30 fps. EA FC **"FC Pro Open"
esports broadcast**, multiple matches back-to-back (e.g. Vejrgang vs Tekkz,
TUGA810 vs YASKOW).

## What was calibrated

The in-match **scoreboard** (top-left broadcast overlay): `score_home`,
`score_away`, `clock`. Coordinates measured against real frames and verified by
drawing the region boxes back onto multiple matches - see
`adapters/ea_fc_26/config/hud.yaml` (default = `26.0-fcpro-broadcast`).

Scores read correctly across matches with different team-name lengths after
widening the score boxes (see finding #3).

## Key findings

1. **This is a broadcast overlay, not the console HUD.** The layout (stacked
   team names, score at right, clock below, player-cams bottom corners) is the
   FC Pro production overlay. A user uploading their own console capture will
   have a *different* HUD. The console layout is stubbed as the `console_hud`
   variant in `hud.yaml` but is **unverified** - calibrate it when console
   footage exists.

2. **360p is at the edge of OCR legibility - the headline result.** Source
   digits are only ~8 px tall. The clock (larger, white-on-black) is clearly
   readable. Single score digits are usually readable but *not always* - the
   away "9" in one match was nearly an indistinct blob even by eye. Expect the
   score OCR to be unreliable on 360p footage.
   → **Recommendation:** require ≥720p, ideally 1080p, uploads. Real console
     captures are 1080p/4K, so this YouTube-quality rip is a worst case; the
     product's real inputs should be far easier. Add an upload-time resolution
     check that warns below ~720p.

3. **The scoreboard box width varies with team-name length**, shifting the score
   ~20 px between matches. Fixed-rect regions must be wide enough to span the
   range (done). A sometimes-included side effect: the small team crest can fall
   inside the score crop. The digit whitelist mitigates this, but a sturdier
   long-term fix is to detect the white box's right edge and right-align the
   score region.

4. **No console-style "match facts" screen appears in this broadcast**, so
   possession/shots/etc. could not be calibrated from this clip. Those stat
   regions remain placeholders. A separate clip that shows a full-time stats
   screen is needed to calibrate them.

## Second clip - `test1.mp4` (1920×1080, 3:00)

Same FC Pro Open broadcast, full HD. Findings:

- **1080p is visually clean.** Score digits and clock are crisp and trivially
  human-readable - the resolution problem from the 360p clip is gone. This is
  the resolution the product should target.
- **The score region must exclude the team crest.** The crest sits immediately
  left of the score digit; a region wide enough to survive box-width variance
  also catches the crest, and single-char OCR then reads the crest as a digit
  (`0`→`9`). Fix: right-align a narrow score region against the box's right edge.
- **This broadcast has multiple overlay states.** When a "GROUP A standings"
  side-panel is shown, the scoreboard shifts ~300px right. Fixed coords only fit
  the no-panel state. (A real console HUD is fixed - this is broadcast-specific.)

### OCR-engine caveat (important)

The quick proxy used here is **tesseract.js**, and it has bottomed out: on 1080p
crops that are *trivially legible by eye*, it returns blanks at 0 confidence and
confuses the stylised digit font (`5`→`S`, `6`→`G`). The clock read `05:06`
correctly, proving the crop→preprocess→OCR path is sound, but tesseract is too
weak to give a fair accuracy number on game fonts.

**Conclusion:** input legibility at 1080p is confirmed; a real accuracy number
requires the **production engine, PaddleOCR** (CRNN-based, far stronger on
stylised/small text), which needs the Docker stack. Do not judge feasibility on
the tesseract proxy numbers.

## MEASURED - PaddleOCR on `test1.mp4` (1080p), the real number

Ran the production engine (PaddleOCR 2.7.3 / paddlepaddle 2.6.2) in Docker via
`tools.cli analyze`, 1 fps, 179 frames:

| | |
|---|---|
| Parse confidence | **0.982** |
| Final score | **1-0 - correct** |
| Goal event | detected at 108s (score 0→1), confidence 0.99 |
| Stat screen | none found (correct for this broadcast) |
| API cost | $0.00 (Stage 1 only) |

**Verdict: at 1080p, local OCR reads the HUD reliably.** The pipeline also
derived the goal timing from score-tracking alone. The `interpret()` running-max
+ confidence filter absorbed the shifted-overlay frames and the crest-in-crop
issue gracefully (bad frames → low confidence → ignored), so those calibration
refinements are nice-to-have, not blockers.

Dependency note: PaddleOCR must be pinned to the 2.x / numpy-1.x era (see
pyproject `[ocr]`). 3.x hit a CPU oneDNN bug and numpy-2 ABI breaks.

## End-to-end stack test (full async path) + a hard correction

Ran the whole async path (create → upload 179 frames → MinIO → Celery worker →
PaddleOCR → Postgres → results API). **The stack works end-to-end.** But running
on downscaled (1280px) frames exposed that the earlier CLI `1-0` was not robust:

| Run | Frames | Score | Reality |
|---|---|---|---|
| CLI | native 1920 | 1-0 | correct, but fragile (crest read clean by luck) |
| E2E v1 (running-max) | 1280 | 1-49 | away crest misread as 49 |
| E2E v2 (corroboration) | 1280 | 0-9 | away crest reads 9 in ≥3 frames; real home 1 lost |

Root causes - **this esports broadcast is an adversarial input**:
1. The away **team crest abuts the score digit**; downscaling merges them and OCR
   reads phantom numbers. Consistent enough to survive corroboration.
2. The broadcast **switches overlay states** (a standings panel shifts the
   scoreboard ~300px), so the post-goal `1` falls outside the calibrated region.

**Correction to the earlier verdict:** OCR feasibility should NOT be judged on
this broadcast overlay. The real product input - a user's own **console** capture
with the standard *fixed* HUD and no crest adjacent to the score - avoids both
failure modes. We still lack a console-HUD clip; that is the input that actually
validates Phase 0 for the product.

Improvements kept from this exercise (general wins):
- `interpret()` now rejects implausible scores (>19) and requires a value to be
  corroborated across ≥3 frames before accepting it - kills single-frame noise.
- Confirmed: full async infra (API/worker/MinIO/Postgres/Redis) works end-to-end.

## Net Phase 0 verdict

- **Resolution:** 360p too low (reject at upload); 1080p legible.
- **Engine + stack:** PaddleOCR reads clean HUD digits well; the full async
  pipeline works end-to-end. Both proven.
- **Open (the real Phase 0 close):** feasibility is still UNVERIFIED on the
  actual product input. Every clip so far is an esports *broadcast* overlay
  (crest-next-to-score, shifting layout) - adversarial and unrepresentative.
  Need a short **1080p console-gameplay clip** (standard fixed HUD) to calibrate
  the `console_hud` variant and measure true accuracy. That is the deciding test.
- **Deferred refinements** (only if broadcast footage is ever a target): exclude
  the crest from the score region; detect overlay state to pick the right coords.

## CONSOLE FOOTAGE - Phase 0 CONCLUDED ✅

Clip: `EA SPORTS FC 26 Gameplay (PS5 UHD).mp4` - 1280×720, 18:39, standard
console HUD (top-left: 3-letter abbrevs, dedicated white score panel, clock
below). Calibrated `console_hud` (now the **default** schema) and verified boxes
on real frames.

**Measured, full match, real PaddleOCR:** final **1-1**, **parse confidence
0.981**, **1119 frames**, **308 scene changes**, **$0.00**. Correct - and it
holds through every replay/cutscene in an 18-minute match, not just a short cut.
Also validated through the full async web path (browser extraction → worker →
Postgres → dashboard).

### The one real bug found - and the fix

Replays/set-pieces inject **high-confidence phantom digits** into the fixed HUD
region. Concretely: a 6-frame corner kick read `home='94' away='8'` at 1.00
confidence while the true score was 1-1. Defenses, in `adapters/ea_fc_26/adapter.py`:

1. Plausibility cap (score ≤ 19) - kills the `94`.
2. Live-frame gating - only frames whose clock parses as MM:SS count.
3. **Final score = trailing-window majority** (end-of-match consensus), not
   `max(confirmed)` - the phantom `8` reverts and never reaches the ending.
4. Events capped at the final score - no phantom goal/concede events.

Regression-locked in `tests/test_scoring.py` (the exact 94/8 scenario, the
implausible two-digit case, a genuine goal, and end-state-not-max).

### Still open (Phase 1 input, not a blocker)
- No **full-time stats screen** appeared in this clip, so possession/shots/passes
  regions remain uncalibrated placeholders. Needs a clip that shows one.
