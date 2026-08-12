"""The three cost-ordered stages.

Stage 1 (local, €0) reads the HUD via OCR and flags candidate frames.
Stage 2 (small vision model) labels only those candidates.
Stage 3 (larger vision model) deep-reads only the handful of important moments.

Stages 2 & 3 are OFF by default (settings flags) so Phase 0 behaviour is
unchanged; when on, every model call is budget-checked BEFORE it runs and the
actual token cost is charged after — the match can never silently overspend.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

import cv2

from core.ai.pricing import actual_cost_usd, estimate_cost_usd, image_tokens
from core.extraction.frames import encode_jpeg
from core.extraction.hud import HudReader
from core.extraction.scene import SceneDetector
from core.models.domain import Event, Insight, Metric
from core.models.enums import EventCategory, MatchStatus, MetricSource, SourceType
from core.pipeline.context import PipelineContext
from core.pipeline.cost import BudgetExceeded
from core.storage.frame_keys import frame_prefix

# Which core categories are worth a Stage 3 deep read. Game-AGNOSTIC — these are
# core categories, not game words.
_IMPORTANT = {EventCategory.SCORE_CHANGE, EventCategory.DISCIPLINE, EventCategory.HIGHLIGHT}

# Schema for a single "map" (segment-read) call in full-match coaching mode.
# The model returns concrete observations AND reads the on-screen scoreboard so
# we can cross-check (and correct) the OCR score from what's actually visible.
# Game-agnostic: home/away/clock are universal scoreboard concepts.
_WINDOW_NOTES_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {"type": "array", "items": {"type": "string"}},
        "score_home": {"type": "integer"},   # visible home score, omit if unclear
        "score_away": {"type": "integer"},   # visible away score, omit if unclear
        "clock": {"type": "string"},         # visible match clock "MM:SS", omit if none
    },
    "required": ["observations"],
}


def _plausible_score(v) -> int | None:
    return v if isinstance(v, int) and 0 <= v <= 19 else None


_STAT_METRICS = {  # stat key -> (label, higher_is_better)
    "shots": ("Shots", True),
    "big_chances": ("Big chances", True),
    "goals_for": ("Goals for", True),
    "goals_against": ("Goals against", False),
    "goals_conceded_from_crosses": ("Conceded from crosses", False),
    "defensive_errors": ("Defensive errors", False),
}


def _compress_video(video_bytes: bytes) -> bytes:
    """Downscale to <=720p @ 15fps, drop audio (ffmpeg) so the upload to Gemini is
    small and fast. Gemini samples ~1fps at medium res, so quality is preserved.
    Falls back to the original bytes on any failure."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.mp4"
        out = Path(tmp) / "out.mp4"
        src.write_bytes(video_bytes)
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                 "-vf", "scale=1280:720:force_original_aspect_ratio=decrease",
                 "-r", "12", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
                 "-an", "-threads", "0", "-movflags", "+faststart", str(out)],
                check=True, timeout=600,
            )
            data = out.read_bytes()
            return data if data else video_bytes
        except Exception:  # noqa: BLE001 - never let compression fail the match
            return video_bytes


def _video_duration(video_bytes: bytes) -> float:
    """Seconds via ffprobe; 0 if unknown."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.mp4"
        src.write_bytes(video_bytes)
        try:
            out = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nk=1:nw=1", str(src)], timeout=60)
            return float(out.decode().strip())
        except Exception:  # noqa: BLE001
            return 0.0


def _read_final_score(video_bytes: bytes, settings) -> dict | None:
    """Read the FINAL score deterministically: sample frames across the WHOLE match,
    send a hi-res scoreboard CROP of the LATE frames to the model (proven reliable,
    unlike OCR of the tiny digits), and take the end-of-match majority."""
    try:
        from core.ai.vision import build_vision
        from core.extraction.rosters import _extract_frames
    except Exception:
        return None
    dur = _video_duration(video_bytes)
    every = max(4.0, dur / 45) if dur else 8.0   # ~45 frames spanning the ENTIRE match
    frames = _extract_frames(video_bytes, every_s=every, max_frames=48)
    if len(frames) < 2:
        return None
    late = frames[len(frames) * 2 // 3:]      # final third — where the final score lives
    vm = build_vision(settings)
    schema = {"type": "object", "properties": {"home": {"type": "integer"},
                                               "away": {"type": "integer"}}}
    prompt = ("This is a crop of an FC 26 match scoreboard (home team on the TOP row, away "
              "team on the BOTTOM row). Read the two score NUMBERS exactly. Reply JSON "
              "{home:int, away:int}. If this is not a normal in-match scoreboard, omit them.")
    reads: list[tuple] = []
    for img in late[-10:]:
        try:
            res = vm.generate(model=settings.stage2_model, prompt=prompt,
                              images_jpeg=[_scoreboard_crop(img)], schema=schema, max_tokens=1500)
            h, a = _plausible_score(res.data.get("home")), _plausible_score(res.data.get("away"))
            if h is not None and a is not None:
                reads.append((h, a))
        except Exception:  # noqa: BLE001
            continue
    if not reads:
        return None
    tail = reads[-max(3, len(reads) // 2):]
    vh, va = _majority([h for h, _ in tail]), _majority([a for _, a in tail])
    return {"home": vh, "away": va} if vh is not None and va is not None else None


def _mmss(secs: int) -> str:
    secs = max(0, int(secs))
    return f"{secs // 60:02d}:{secs % 60:02d}"


def _clamp_point_times(items: list[str], dur_s: float) -> list[str]:
    """Strip trailing (MM:SS) timestamps that fall outside the video's real duration
    — the single-call model sometimes reports the IN-GAME match clock (0-90') by
    mistake, which would make a 'jump to moment' seek to nowhere. Keeps only the
    timestamps that are actually within the clip."""
    if not dur_s or dur_s <= 0:
        return items
    out: list[str] = []
    for it in items:
        m = re.search(r"\s*\(((?:\d{1,3}:\d{2})(?:\s*,\s*\d{1,3}:\d{2})*)\)\s*$", str(it))
        if not m:
            out.append(it)
            continue
        good = [t.strip() for t in m.group(1).split(",")
                if (_parse_secs(t.strip()) or 0) <= dur_s + 3]
        base = str(it)[:m.start()].rstrip()
        out.append(base + (f" ({', '.join(good)})" if good else ""))
    return out


def _reconcile_goals(det_goals: list[dict], model_goals: list[dict]) -> list[dict]:
    """Fuse the deterministic goal log (authoritative time + scored/conceded) with
    the model's descriptions. Time and type ALWAYS come from the scoreboard read;
    the model only supplies 'summary'/'fix', matched positionally. Falls back to
    the model's own list when we have no deterministic log."""
    if not det_goals:
        return model_goals
    out: list[dict] = []
    for i, g in enumerate(det_goals):
        m = model_goals[i] if i < len(model_goals) else {}
        summary = str(m.get("summary") or "").strip()
        entry = {
            "time": g["time"],
            "type": g["type"],
            "summary": summary or ("Goal conceded — see the moment for detail."
                                   if g["type"] == "conceded" else "Goal scored."),
        }
        fix = str(m.get("fix") or "").strip()
        if g["type"] == "conceded" and fix:
            entry["fix"] = fix
        if g.get("deep"):  # per-goal deep re-read (attached to the deterministic entry)
            entry["deep"] = g["deep"]
        out.append(entry)
    return out


def _read_score_timeline(video_bytes: bytes, settings) -> dict | None:
    """Deterministically reconstruct WHEN each goal happened by reading the
    scoreboard across the WHOLE match and watching the score change.

    The score is monotonic non-decreasing during a match, so every confirmed
    increment of the home (or away) number is exactly one home (or away) goal —
    giving us the goal COUNT, TIME and SIDE without asking the model to guess
    (long matches make it hallucinate a goal-count). Robust to the same phantom
    digits `interpret()` fights: a value is only trusted once it forms a PLATEAU
    (seen >=2 reads), and a read that DECREASES either side is ignored (replays /
    cutscenes showing an older or garbage score).

    Returns {"final": {home, away}, "goals": [{"secs", "time", "side"}...]} or
    None if the scoreboard couldn't be read.
    """
    try:
        from concurrent.futures import ThreadPoolExecutor
        from core.ai.vision import build_vision
        from core.extraction.rosters import _extract_frames
    except Exception:
        return None
    dur = _video_duration(video_bytes)
    # Dense enough that each plateau spans >=2 reads (goals are minutes apart), but
    # bounded so we don't spray the rate limit. Frame budget is configurable so the
    # lighter "middle mode" can trade goal-timing precision for fewer requests.
    n = max(8, int(getattr(settings, "gemini_score_max_frames", 60)))
    every = max(4.0, dur / n) if dur else 6.0
    frames = _extract_frames(video_bytes, every_s=every, max_frames=n)
    if len(frames) < 3:
        return None
    vm = build_vision(settings)
    schema = {"type": "object", "properties": {"home": {"type": "integer"},
                                               "away": {"type": "integer"}}}
    prompt = ("This is a crop of an FC 26 match scoreboard (home team on the TOP row, away "
              "team on the BOTTOM row). Read the two score NUMBERS exactly. Reply JSON "
              "{home:int, away:int}. If this is not a normal in-match scoreboard, omit them.")

    def _read_one(pair):
        idx, img = pair
        try:
            res = vm.generate(model=settings.stage2_model, prompt=prompt,
                              images_jpeg=[_scoreboard_crop(img)], schema=schema, max_tokens=1500)
            h, a = _plausible_score(res.data.get("home")), _plausible_score(res.data.get("away"))
            cost = getattr(res, "cost_usd", 0.0) or 0.0
            if h is not None and a is not None:
                return idx, (h, a), cost
            return idx, None, cost
        except Exception:  # noqa: BLE001
            return idx, None, 0.0

    seq: list = [None] * len(frames)
    read_cost = 0.0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for idx, val, cost in ex.map(_read_one, list(enumerate(frames))):
            seq[idx] = val
            read_cost += cost

    # Compress consecutive equal reads (ignoring gaps) into plateaus, keeping the
    # first frame index (=timestamp) of each plateau.
    runs: list[dict] = []
    for idx, v in enumerate(seq):
        if v is None:
            continue
        if runs and runs[-1]["v"] == v:
            runs[-1]["count"] += 1
        else:
            runs.append({"v": v, "start": idx, "count": 1})
    # Only trust plateaus seen at least twice -> drops single-frame phantoms.
    kept = [r for r in runs if r["count"] >= 2]
    if not kept:
        return None

    # Walk the plateaus enforcing monotonic non-decreasing score; each accepted
    # increase emits one goal per unit, on the side that went up, at the plateau's
    # start time.
    goals: list[dict] = []
    ch = ca = 0
    for r in kept:
        h, a = r["v"]
        if h < ch or a < ca or (h == ch and a == ca):
            continue
        secs = int(round(r["start"] * every))
        for _ in range(h - ch):
            goals.append({"secs": secs, "time": _mmss(secs), "side": "home"})
        for _ in range(a - ca):
            goals.append({"secs": secs, "time": _mmss(secs), "side": "away"})
        ch, ca = h, a
    if ch == 0 and ca == 0:
        return None
    return {"final": {"home": ch, "away": ca}, "goals": goals,
            "cost_usd": round(read_cost, 6), "granularity_s": every}


def _extract_window_jpegs(video_bytes: bytes, center_s: float, pre_s: float,
                          post_s: float, fps: float) -> list[bytes]:
    """Extract JPEG frames from the [center-pre, center+post] window of the video
    (via a fast ffmpeg seek) — the few seconds around a single goal."""
    start = max(0.0, center_s - pre_s)
    span = pre_s + post_s
    out: list[bytes] = []
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        vid = p / "m.mp4"
        vid.write_bytes(video_bytes)
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{start:.2f}", "-t", f"{span:.2f}", "-i", str(vid),
                 "-vf", f"fps={fps}", "-frames:v", "20", "-q:v", "3",
                 str(p / "w%03d.jpg")],
                check=True, timeout=120,
            )
        except Exception:  # noqa: BLE001
            return out
        for f in sorted(p.glob("w*.jpg")):
            img = cv2.imread(str(f))
            if img is not None:
                out.append(encode_jpeg(img, max_width=960, quality=80))
    return out


_DEEP_GOAL_SCHEMA = {
    "type": "object",
    "properties": {
        "defender": {"type": "string"},      # your player most at fault / involved
        "what_happened": {"type": "string"},  # the sequence that led to the goal
        "root_cause": {"type": "string"},     # WHY it broke down (the real mistake)
        "fix": {"type": "string"},            # exact FC26 input / positioning fix
    },
    "required": ["what_happened", "root_cause", "fix"],
}


def _lite_report_schema() -> dict:
    """Report schema for SINGLE-CALL mode: the one video call fills the coaching
    lists directly (plain strings — no evidence-id mapping, since there is no
    separate observation log to cite)."""
    strs = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "strengths": strs, "recurring_mistakes": strs,
            "positioning_issues": strs, "decision_patterns": strs, "practice_drills": strs,
            "weakness_tags": strs,
            "score": {"type": "object",
                      "properties": {"home": {"type": "integer"}, "away": {"type": "integer"}}},
            "formation": {"type": "string"},
            "goals": {"type": "array", "items": {"type": "object", "properties": {
                "time": {"type": "string"}, "type": {"type": "string"},
                "summary": {"type": "string"}, "fix": {"type": "string"},
            }, "required": ["time", "type", "summary"]}},
            "stats": {"type": "object", "properties": {
                "shots": {"type": "integer"}, "big_chances": {"type": "integer"},
                "goals_for": {"type": "integer"}, "goals_against": {"type": "integer"},
                "goals_conceded_from_crosses": {"type": "integer"},
                "defensive_errors": {"type": "integer"},
            }},
        },
        "required": ["summary", "strengths", "recurring_mistakes",
                     "positioning_issues", "decision_patterns", "practice_drills"],
    }


def _deep_read_goals(video_bytes: bytes, settings, goals: list[dict], side: str,
                     my_roster: list[str], opp_roster: list[str],
                     granularity_s: float = 0.0) -> tuple[list[dict], float]:
    """Re-watch the seconds around the most important CONCEDED goals with the
    stronger model and attach a per-goal breakdown. Returns (goals, cost).

    Deterministic goal times (from the scoreboard timeline) tell us exactly WHERE
    to look, so instead of one shallow pass over 20 minutes we spend the model on
    the handful of moments that actually decided the match."""
    from core.ai.vision import build_vision

    conceded = [g for g in goals if g.get("type") == "conceded" and "secs" in g]
    if not conceded:
        return goals, 0.0
    # Prioritise: spread the picks across the match (early collapses matter as much
    # as late ones), capped so we stay cheap.
    conceded.sort(key=lambda g: g["secs"])
    n = min(settings.gemini_deep_goals_max, len(conceded))
    if n <= 0:
        return goals, 0.0
    step = len(conceded) / n
    picks = [conceded[min(len(conceded) - 1, int(i * step))] for i in range(n)]

    vm = build_vision(settings)
    roster_line = (f"Your squad: {', '.join(my_roster)}. " if my_roster else "")
    opp_line = (f"Opponent: {', '.join(opp_roster)}. " if opp_roster else "")
    # The goal is timed to within one sampling interval (the scoreboard shows the
    # NEW score just AFTER the goal), so look back at least that far to be sure the
    # build-up is in-frame. Pick an fps that spreads ~14 frames across the window.
    pre_s = max(settings.gemini_deep_goals_pre_s, granularity_s + 3.0)
    post_s = settings.gemini_deep_goals_post_s
    span = max(1.0, pre_s + post_s)
    fps = max(0.4, min(settings.gemini_deep_goals_fps, 14.0 / span))
    cost = 0.0
    for g in picks:
        frames = _extract_window_jpegs(
            video_bytes, float(g["secs"]), pre_s, post_s, fps)
        if not frames:
            continue
        prompt = (
            f"These frames are the seconds LEADING UP TO a goal the opponent scored against "
            f"the '{side}' player at {g.get('time', '')} (in time order). {roster_line}{opp_line}"
            f"Coach the '{side}' player on THIS goal specifically. Identify which of THEIR "
            f"defenders was at fault (by name if visible, else by role), what broke down in the "
            f"sequence, the ROOT CAUSE (the actual mistake), and the EXACT FC 26 fix (the specific "
            f"button/stick input or positioning) that would have prevented it. Do not invent names "
            f"outside the squads given. Reply strict JSON with keys defender, what_happened, "
            f"root_cause, fix."
        )
        try:
            res = vm.generate(model=settings.gemini_video_synth_model, prompt=prompt,
                              images_jpeg=frames, schema=_DEEP_GOAL_SCHEMA, max_tokens=1200)
            cost += getattr(res, "cost_usd", 0.0) or 0.0
            data = res.data or {}
            if data.get("what_happened"):
                g["deep"] = {
                    "defender": str(data.get("defender") or "").strip(),
                    "what_happened": str(data.get("what_happened") or "").strip(),
                    "root_cause": str(data.get("root_cause") or "").strip(),
                    "fix": str(data.get("fix") or "").strip(),
                }
        except Exception:  # noqa: BLE001 - a failed deep read must not fail the match
            continue
    return goals, round(cost, 6)


def _stats_to_metrics(ctx: PipelineContext, stats: dict) -> None:
    """Turn observed stats into Metric objects so the trends system tracks them
    across matches. Additive — doesn't affect the coaching text."""
    if not stats:
        return
    metrics = list(ctx.match.metrics)
    for key, (label, hib) in _STAT_METRICS.items():
        v = stats.get(key)
        if isinstance(v, (int, float)):
            metrics.append(Metric(
                key=key, label=label, value=float(v), higher_is_better=hib,
                source=MetricSource.MODEL, confidence=0.6,
            ))
    ctx.match.metrics = metrics


def _video_report_schema() -> dict:
    """Coaching report where every point cites the observation IDs it's based on.
    Citing IDs is easy for the model AND lets us attach real timestamps ourselves
    (deterministic) — grounding the point and exposing divergence from the log."""
    item = {
        "type": "object",
        "properties": {"point": {"type": "string"},
                       "evidence_ids": {"type": "array", "items": {"type": "integer"}}},
        "required": ["point", "evidence_ids"],
    }
    arr = {"type": "array", "items": item}
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"}, "strengths": arr, "recurring_mistakes": arr,
            "positioning_issues": arr, "decision_patterns": arr, "practice_drills": arr,
            # Weakness tags from the controlled vocabulary — fuel for the "learns
            # you" longitudinal loop (aggregated across the player's matches).
            "weakness_tags": {"type": "array", "items": {"type": "string"}},
            # Final score fallback — the synthesis reliably knows it even when the
            # watch pass fails to fill the score field.
            "score": {"type": "object",
                      "properties": {"home": {"type": "integer"}, "away": {"type": "integer"}}},
            # The user's formation if identifiable (e.g. "4-2-3-1") — aggregated
            # across matches to learn how they set up.
            "formation": {"type": "string"},
            # Goal-by-goal breakdown (NEW section — additive, does not change the 5
            # coaching lists). One entry per goal, scored or conceded.
            "goals": {"type": "array", "items": {"type": "object", "properties": {
                "time": {"type": "string"},
                "type": {"type": "string"},          # "scored" | "conceded"
                "summary": {"type": "string"},        # what happened + who
                "fix": {"type": "string"},            # for conceded: how to prevent it
            }, "required": ["time", "type", "summary"]}},
            # Observed match stats (integers) -> quantify + feed trends.
            "stats": {"type": "object", "properties": {
                "shots": {"type": "integer"}, "big_chances": {"type": "integer"},
                "goals_for": {"type": "integer"}, "goals_against": {"type": "integer"},
                "goals_conceded_from_crosses": {"type": "integer"},
                "defensive_errors": {"type": "integer"},
            }},
        },
        "required": ["summary", "strengths", "recurring_mistakes",
                     "positioning_issues", "decision_patterns", "practice_drills"],
    }


def _history_block(history: dict | None, vocab: list[dict]) -> str:
    """Prompt block: this player's recurring problems across past matches + the
    weakness-tag vocabulary to classify this match."""
    tags = ", ".join(f"{v['tag']}" for v in (vocab or [])[:20])
    out = [f"WEAKNESS TAGS (choose 'weakness_tags' ONLY from this list): {tags}"] if tags else []
    if not history:
        return "\n".join(out)
    n = history.get("matches", 0)
    # Learned player profile (personalization across matches).
    prof = []
    if history.get("squad"):
        prof.append("Their regular squad (from past matches): " + ", ".join(history["squad"])
                    + " — use these names; treat a name not here (and not in this match's roster) "
                    "as the opponent or a misread.")
    if history.get("formation"):
        prof.append(f"They usually line up in {history['formation']} — coach within that shape.")
    if prof:
        out.append("\nWHO THIS PLAYER IS (learned over " + str(n) + " matches):\n" + "\n".join(prof))
    if history.get("issues"):
        recur = "; ".join(f"{i['tag']} (seen in {i['count']}/{n})" for i in history["issues"])
        out.append(
            f"\nTHEIR RECURRING PROBLEMS: {recur}.\n"
            f"If any of these show up AGAIN this match, SAY it is recurring and that earlier "
            f"advice hasn't stuck — escalate or try a DIFFERENT fix, don't just repeat yourself."
        )
        if history.get("recent_advice"):
            out.append("Advice you gave them very recently (check if it worked): "
                       + " | ".join(history["recent_advice"][:4]))
    return "\n".join(out)


def _obs_time(observation: str) -> str:
    m = re.match(r"\s*\[([^\]]+)\]", observation or "")
    return m.group(1).strip() if m else ""


def _parse_secs(t: str) -> int | None:
    m = re.match(r"(\d{1,3}):(\d{2})", t or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


_STOPWORDS = {
    "the", "and", "with", "his", "her", "their", "into", "from", "for", "you",
    "your", "ball", "play", "player", "team", "side", "match", "that", "this",
    "near", "around", "towards", "back", "make", "made", "uses", "using",
}


def _keywords(note: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (note or "").lower())
            if len(w) > 3 and w not in _STOPWORDS}


def _merge_passes(pass_lists: list[list[str]], window: int = 12) -> list[dict]:
    """Cross-pass consensus: cluster observations from DIFFERENT viewings that are
    close in time and share keywords. Each merged item gets a 'support' = how many
    independent viewings saw it. Recurring observations (support>=2) are trusted;
    one-off (support==1) are likely hallucinations."""
    items: list[list] = []  # [secs, note, pass_idx, keywords]
    for pi, lst in enumerate(pass_lists):
        for o in lst:
            secs = _parse_secs(_obs_time(o))
            note = re.sub(r"^\s*\[[^\]]*\]\s*", "", o).strip()
            if secs is None or not note:
                continue
            items.append([secs, note, pi, _keywords(note)])
    items.sort(key=lambda x: x[0])
    used = [False] * len(items)
    merged: list[dict] = []
    for i, it in enumerate(items):
        if used[i]:
            continue
        cluster = [it]
        used[i] = True
        for j in range(i + 1, len(items)):
            if used[j]:
                continue
            if items[j][0] - it[0] > window:
                break
            if items[j][2] in {c[2] for c in cluster}:
                continue  # same viewing already represented — want cross-pass support
            if len(it[3] & items[j][3]) >= 2:
                cluster.append(items[j])
                used[j] = True
        rep = max(cluster, key=lambda c: len(c[1]))  # longest (most detailed) note
        merged.append({"secs": it[0], "note": rep[1], "support": len({c[2] for c in cluster})})
    merged.sort(key=lambda m: m["secs"])
    return merged


def _flatten_report(d: dict, observations: list[str], drop_unsupported: bool = False) -> dict:
    """Turn {point, evidence_ids[]} items into 'point (12:30, 34:10)' strings by
    mapping each id (1-based) to its observation timestamp. Unsupported non-drill
    points can be dropped to cut confabulation."""
    times = [_obs_time(o) for o in observations]

    def fmt(items, is_drill=False):
        out = []
        for it in items or []:
            if isinstance(it, dict):
                pt = str(it.get("point", "")).strip()
                if not pt:
                    continue
                ts, seen = [], set()
                for i in it.get("evidence_ids") or []:
                    if isinstance(i, int) and 1 <= i <= len(times) and times[i - 1] and times[i - 1] not in seen:
                        ts.append(times[i - 1])
                        seen.add(times[i - 1])
                if not ts and not is_drill and drop_unsupported:
                    continue
                out.append(f"{pt} ({', '.join(ts)})" if ts else pt)
            elif it:
                out.append(str(it))
        return out
    return {
        "summary": str(d.get("summary", "")),
        "strengths": fmt(d.get("strengths")),
        "recurring_mistakes": fmt(d.get("recurring_mistakes")),
        "positioning_issues": fmt(d.get("positioning_issues")),
        "decision_patterns": fmt(d.get("decision_patterns")),
        "practice_drills": fmt(d.get("practice_drills"), is_drill=True),
    }


def _format_observations(raw: list) -> list[str]:
    """Normalise watch-pass observations to '[MM:SS] note' strings. Tolerates both
    the timestamped object form and a bare string (older/looser model output)."""
    out: list[str] = []
    for o in raw:
        if isinstance(o, dict):
            note = str(o.get("note") or "").strip()
            if not note:
                continue
            t = str(o.get("time") or "").strip()
            out.append(f"[{t}] {note}" if t else note)
        elif o:
            out.append(str(o).strip())
    return out


def _scoreboard_crop(image, wfrac: float = 0.24, hfrac: float = 0.20, upscale: int = 3) -> bytes:
    """A high-res zoom of the top-left corner, where both the console HUD and the
    FC-Pro broadcast overlay put the scoreboard. Downscaled full frames make the
    small score digits unreadable (a '1' misreads as '8'); this crop is sent at
    native resolution and upscaled so the model can read them reliably."""
    hh, ww = image.shape[:2]
    crop = image[0:max(1, int(hh * hfrac)), 0:max(1, int(ww * wfrac))]
    if upscale > 1:
        crop = cv2.resize(crop, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    return encode_jpeg(crop, max_width=10_000, quality=88)


def _majority(vals: list[int]) -> int | None:
    if not vals:
        return None
    tally: dict[int, int] = {}
    for v in vals:
        tally[v] = tally.get(v, 0) + 1
    return max(tally, key=lambda v: (tally[v], v))


class Stage(ABC):
    name: str

    @abstractmethod
    def enabled(self, ctx: PipelineContext) -> bool: ...

    @abstractmethod
    def run(self, ctx: PipelineContext) -> None: ...


def _estimate_call(vision, model: str, est_input: int, max_output: int) -> float:
    """Worst-case cost of a call BEFORE it runs, priced by the actual engine."""
    if getattr(vision, "free", False):
        return 0.0
    in_rate = getattr(vision, "input_usd_per_mtok", None)
    if in_rate is not None:  # OpenAI-compatible engine carries its own rates
        return (est_input * in_rate + max_output * getattr(vision, "output_usd_per_mtok", 0)) / 1_000_000.0
    return estimate_cost_usd(model, est_input, max_output)  # Anthropic / known models


def _charge_call(vision, model: str, res) -> float:
    """Actual cost of a completed call: prefer the engine's own figure."""
    if getattr(vision, "free", False):
        return 0.0
    if getattr(res, "cost_usd", 0):
        return res.cost_usd
    return actual_cost_usd(model, res.input_tokens, res.output_tokens)


def _evenly_sample(items: list[int], k: int) -> list[int]:
    if k <= 0 or len(items) <= k:
        return items
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def _evenly_sample_frames(items: list, k: int) -> list:
    if k <= 0 or len(items) <= k:
        return list(items)
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def _chunk(items: list, size: int) -> list[list]:
    """Split into consecutive windows of `size` (last may be shorter)."""
    if size <= 0:
        return [list(items)]
    return [items[i : i + size] for i in range(0, len(items), size)]


class Stage1LocalExtraction(Stage):
    """Frames -> scene diff -> HUD OCR -> adapter.interpret(). No model, no cost.
    Also flags candidate frames for Stage 2."""

    name = "stage1_local_extraction"

    def enabled(self, ctx: PipelineContext) -> bool:
        return True

    def run(self, ctx: PipelineContext) -> None:
        # Dispatch on the SOURCE TYPE (a core concept), never on the game. Video
        # goes through frame OCR; replay/API sources are parsed by the adapter's
        # ingest(). Adding a non-video game touches only /adapters.
        if ctx.match.source_type != SourceType.VIDEO:
            self._run_non_video(ctx)
            return
        ctx.emit(self.name, "running", "scene detection")
        scene = SceneDetector().analyze(ctx.frames)
        ctx.emit(
            self.name,
            "running",
            f"{len(scene.scene_changes)} scene changes, "
            f"{len(scene.static_runs)} static runs; reading HUD",
        )

        schema = ctx.adapter.hud_schema(ctx.match.capture)
        reader = HudReader(ctx.ocr)
        readings, stat_frames = reader.read(ctx.frames, schema, scene)

        parsed = ctx.adapter.interpret(readings)

        m = ctx.match
        m.metrics = parsed.metrics
        m.events = parsed.events
        m.outcome = parsed.outcome
        m.parse_confidence = parsed.parse_confidence
        m.warnings.extend(parsed.warnings)

        # Hand candidate frames to Stage 2 (scene changes + stat screens).
        ctx.stat_frames = stat_frames
        ctx.candidate_frames = sorted(set(scene.scene_changes) | set(stat_frames))

        if parsed.parse_confidence < 0.4:
            m.warnings.append(
                f"Low HUD parse confidence ({parsed.parse_confidence:.2f}). "
                f"HUD schema '{schema.game_id}@{schema.edition} v{schema.schema_version}' "
                f"may be stale (game patch?) or coordinates need calibration."
            )
        ctx.emit(
            self.name,
            "done",
            f"{len(parsed.metrics)} metrics, {len(parsed.events)} events, "
            f"{len(ctx.candidate_frames)} candidate frames, "
            f"confidence={parsed.parse_confidence:.2f}",
            stat_frames=stat_frames,
        )


    def _run_non_video(self, ctx: PipelineContext) -> None:
        """Replay/API source: the adapter parses raw bytes straight into
        metrics/events/outcome — no frames, no OCR, no cost."""
        ctx.emit(self.name, "running", f"ingesting {ctx.match.source_type.value} source")
        if ctx.source_bytes is None:
            ctx.match.warnings.append("no source data uploaded")
            ctx.emit(self.name, "failed", "no source bytes")
            return
        parsed = ctx.adapter.ingest(ctx.source_bytes)
        m = ctx.match
        m.metrics = parsed.metrics
        m.events = parsed.events
        m.outcome = parsed.outcome
        m.parse_confidence = parsed.parse_confidence
        m.warnings.extend(parsed.warnings)
        ctx.emit(
            self.name, "done",
            f"{len(parsed.metrics)} metrics, {len(parsed.events)} events (replay/API)",
        )


class Stage2CheapEvents(Stage):
    """Small/fast vision model over ONLY the Stage 1 candidate frames. Labels each
    (goal/card/sub/replay/…) and adds non-score events the OCR can't see."""

    name = "stage2_cheap_events"

    def enabled(self, ctx: PipelineContext) -> bool:
        return ctx.settings.enable_stage_2

    def run(self, ctx: PipelineContext) -> None:
        if ctx.vision is None:
            ctx.match.warnings.append("Stage 2 enabled but no vision model configured; skipped.")
            ctx.emit(self.name, "skipped", "no vision model")
            return

        s = ctx.settings
        adapter = ctx.adapter
        schema = adapter.stage2_label_schema()
        prompt = adapter.stage_prompt(2) or "Classify this frame."
        vocab = adapter.event_type_map()

        candidates = _evenly_sample(ctx.candidate_frames, s.stage2_max_candidates)
        ctx.emit(self.name, "running", f"classifying {len(candidates)} candidate frames")

        classified = added = 0
        for idx in candidates:
            frame = ctx.frame_by_index(idx)
            if frame is None or frame.image is None:
                continue
            jpeg = encode_jpeg(frame.image, max_width=640, quality=60)
            w, h = frame.size
            eh = int(round(h * (640 / w))) if w > 640 else h
            est_in = image_tokens(min(640, w), eh) + len(prompt) // 4 + 64
            est = _estimate_call(ctx.vision, s.stage2_model, est_in, 64)
            if ctx.cost.remaining < est:
                ctx.match.warnings.append(
                    f"Stage 2 stopped early to stay under budget "
                    f"(${ctx.cost.remaining:.4f} left); {classified}/{len(candidates)} frames classified."
                )
                break

            res = ctx.vision.generate(
                model=s.stage2_model, prompt=prompt, images_jpeg=[jpeg], schema=schema, max_tokens=64
            )
            ctx.cost.charge(f"stage2:{s.stage2_model}", _charge_call(ctx.vision, s.stage2_model, res))
            classified += 1

            label = res.data.get("label")
            conf = float(res.data.get("confidence") or 0.0)
            etd = vocab.get(label)
            # Skip 'in_play' and SCORE_CHANGE (OCR is authoritative for the score);
            # Stage 2's value is the events OCR can't read (cards, subs, replays).
            if etd and conf >= 0.5 and etd.category != EventCategory.SCORE_CHANGE:
                ctx.match.events.append(
                    Event(
                        timestamp_ms=frame.timestamp_ms,
                        category=etd.category,
                        game_event_type=label,
                        confidence=conf,
                        frame_refs=[frame.key],
                    )
                )
                added += 1

        ctx.match.events.sort(key=lambda e: e.timestamp_ms)
        ctx.emit(
            self.name, "done",
            f"classified {classified} frames, +{added} events, cost=${ctx.cost.total:.4f}",
        )


class Stage3CoachingReport(Stage):
    """ONE whole-match coaching report (not per-goal). Samples frames across the
    match (weighted to key moments) + the OCR facts, and asks the large model for
    recurring mistakes, positioning issues, decision patterns, and drills."""

    name = "stage3_coaching_report"

    def enabled(self, ctx: PipelineContext) -> bool:
        return ctx.settings.enable_stage_3

    def run(self, ctx: PipelineContext) -> None:
        if ctx.vision is None:
            ctx.match.warnings.append("Stage 3 enabled but no vision model configured; skipped.")
            ctx.emit(self.name, "skipped", "no vision model")
            return

        s = ctx.settings
        live = sorted((f for f in ctx.frames if f.image is not None), key=lambda f: f.timestamp_ms)
        if not live:
            ctx.emit(self.name, "skipped", "no frames to review")
            return

        # "full" reads the WHOLE match segment-by-segment when there are enough
        # frames to be worth it; otherwise (or in "sample" mode) one pooled call.
        if s.coaching_mode == "full" and len(live) > s.coaching_window_frames:
            self._run_full(ctx, live)
        else:
            self._run_sample(ctx)

    def _emit_report(self, ctx, d, *, frames_reviewed, segments, model, before):
        ctx.match.insights = [
            Insight(
                scope="match",
                kind="coaching_report",
                summary=str(d.get("summary", "")),
                payload={
                    "strengths": d.get("strengths", []),
                    "recurring_mistakes": d.get("recurring_mistakes", []),
                    "positioning_issues": d.get("positioning_issues", []),
                    "decision_patterns": d.get("decision_patterns", []),
                    "practice_drills": d.get("practice_drills", []),
                    "player_side": ctx.match.capture.get("player_side", "unknown"),
                    "frames_reviewed": frames_reviewed,
                    "segments_read": segments,
                },
                model=model,
                cost_usd=round(ctx.cost.total - before, 6),
            )
        ]
        ctx.emit(self.name, "done", f"coaching report ready, cost=${ctx.cost.total:.4f}")

    # --- "sample" mode: one pooled call over frames spread across the match -----
    def _run_sample(self, ctx: PipelineContext) -> None:
        s, adapter = ctx.settings, ctx.adapter
        frames = self._select_frames(ctx, s.coaching_max_frames)
        if not frames:
            ctx.emit(self.name, "skipped", "no frames to review")
            return

        prompt = (
            self._facts_preamble(ctx.match) + "\n\n" + adapter.coaching_playbook()
            + "\n\n" + (adapter.stage_prompt(3) or "Coach this match.")
        )
        schema = adapter.coaching_schema()

        jpegs, est_in = [], len(prompt) // 4 + 300
        w0 = s.coaching_frame_width
        for f in frames:
            jpegs.append(encode_jpeg(f.image, max_width=w0, quality=75))
            w, h = f.size
            eh = int(round(h * (w0 / w))) if w > w0 else h
            est_in += image_tokens(min(w0, w), eh)

        est = _estimate_call(ctx.vision, s.stage3_model, est_in, 1500)
        if ctx.cost.remaining < est:
            ctx.match.warnings.append(
                f"Skipped coaching report to stay under budget (${ctx.cost.remaining:.4f} left)."
            )
            ctx.emit(self.name, "skipped", "budget")
            return

        ctx.emit(self.name, "running", f"coaching review over {len(frames)} frames")
        before = ctx.cost.total
        try:
            res = ctx.vision.generate(
                model=s.stage3_model, prompt=prompt, images_jpeg=jpegs, schema=schema, max_tokens=1500
            )
            ctx.cost.charge(f"stage3:{s.stage3_model}", _charge_call(ctx.vision, s.stage3_model, res))
        except BudgetExceeded:
            ctx.match.warnings.append("Skipped coaching report to stay under budget.")
            ctx.emit(self.name, "skipped", "budget")
            return
        self._emit_report(ctx, res.data, frames_reviewed=len(frames), segments=1,
                          model=res.model, before=before)

    # --- "full" mode: map (read each segment) then reduce (synthesise) ----------
    # Cost design: the MAP step (read each segment) uses the cheap model
    # (stage2_model, e.g. Haiku) — perception is cheap; the REDUCE step (one
    # synthesis over all notes) uses the strong model (stage3_model, e.g. Sonnet)
    # — reasoning is where the quality is. Both are budget-checked and charged.
    def _run_full(self, ctx: PipelineContext, live) -> None:
        s, adapter = ctx.settings, ctx.adapter
        side = ctx.match.capture.get("player_side", "the player")
        lens = adapter.stage_prompt(3) or "Coach this match."
        schema = adapter.coaching_schema()
        reader = s.stage2_model or s.stage3_model  # cheap perception model
        w0 = s.coaching_frame_width

        cap = s.coaching_window_frames * s.coaching_max_windows
        dense = _evenly_sample_frames(live, cap) if len(live) > cap else live
        windows = _chunk(dense, s.coaching_window_frames)

        before = ctx.cost.total
        notes: list[str] = []
        boards: list[tuple[int, int]] = []  # (home, away) read from the HUD, in order
        for wi, win in enumerate(windows):
            # IMAGE 1 is a hi-res zoom of the scoreboard corner (for an accurate
            # score read); the rest are the downscaled play frames (for tactics).
            jpegs = [_scoreboard_crop(win[len(win) // 2].image)]
            est_in = 300 + image_tokens(600, 360)
            for f in win:
                jpegs.append(encode_jpeg(f.image, max_width=w0, quality=72))
                w, h = f.size
                eh = int(round(h * (w0 / w))) if w > w0 else h
                est_in += image_tokens(min(w0, w), eh)
            # Token ceiling is generous: thinking models (Gemini 3) spend output
            # tokens on reasoning first, so a low cap truncates the JSON.
            seg_max = 2000
            if ctx.cost.remaining < _estimate_call(ctx.vision, reader, est_in, seg_max):
                ctx.match.warnings.append(
                    f"Stopped reading the match early at segment {wi + 1}/{len(windows)} to stay under budget."
                )
                break
            t0, t1 = win[0].timestamp_ms // 1000, win[-1].timestamp_ms // 1000
            span = f"{t0 // 60:02d}:{t0 % 60:02d}-{t1 // 60:02d}:{t1 % 60:02d}"
            # Note: we DON'T assert the OCR score here — the model READS the
            # scoreboard itself so we can correct OCR from what's on screen.
            prompt = (
                f"You are analysing the '{side}' player's match. IMAGE 1 is a ZOOMED crop of the "
                f"top-left scoreboard; IMAGES 2+ are consecutive play frames from ONE segment "
                f"({span}), in order.\n"
                f"1) From IMAGE 1, READ the scoreboard exactly: the TOP number is the home score, "
                f"the number BELOW it is the away score, and the match clock. Read the digits "
                f"literally (a thin '1' is not an '8'). If IMAGE 1 is not a normal in-match "
                f"scoreboard (intro/replay/menu/cutscene), omit the scores.\n"
                f"2) From IMAGES 2+, note concrete, specific things you SEE about the '{side}' "
                f"player: positioning, decisions, passing/movement, defensive shape — both good "
                f"and bad. Be terse and factual.\n\n"
                f"Reply with ONLY a flat JSON object using these EXACT keys (do NOT nest under "
                f"'scoreboard'): {{\"score_home\": <int or omit>, \"score_away\": <int or omit>, "
                f"\"clock\": \"MM:SS\" or omit, \"observations\": [\"...\"]}}\n\n{lens}"
            )
            ctx.emit(self.name, "running", f"reading segment {wi + 1}/{len(windows)} ({span})")
            try:
                res = ctx.vision.generate(
                    model=reader, prompt=prompt, images_jpeg=jpegs,
                    schema=_WINDOW_NOTES_SCHEMA, max_tokens=seg_max,
                )
                ctx.cost.charge(f"stage3-seg:{reader}", _charge_call(ctx.vision, reader, res))
            except BudgetExceeded:
                ctx.match.warnings.append(
                    f"Stopped reading the match at segment {wi + 1}/{len(windows)} (budget)."
                )
                break
            h, a = _plausible_score(res.data.get("score_home")), _plausible_score(res.data.get("score_away"))
            if h is not None and a is not None:
                boards.append((h, a))
            obs = res.data.get("observations") or ([res.text.strip()] if res.text.strip() else [])
            if obs:
                notes.append(f"[{span}] " + " | ".join(str(o) for o in obs if o))

        if not notes:  # couldn't read anything within budget — try the pooled read as a fallback
            self._run_sample(ctx)
            return

        # Cross-check the OCR score against what the model actually saw on the HUD.
        # End-of-match consensus (trailing boards) corrects OCR phantoms (e.g. an
        # away '1' misread as '8'). Only override with real support (>=2 agreeing).
        self._reconcile_score(ctx, boards, side)

        # Reduce: synthesise ONE report from the chronological notes + corrected facts.
        facts = self._facts_preamble(ctx.match)
        playbook = adapter.coaching_playbook(" ".join(notes))
        syn = (
            f"{facts}\n\n{playbook}\n\nBelow are chronological observations from reading the WHOLE "
            f"match segment by segment:\n\n" + "\n".join(notes) +
            f"\n\n{lens}\n\nSynthesise ONE coaching report over the WHOLE match — find the "
            f"RECURRING patterns across segments, not one-off moments. Be BALANCED: include "
            f"what the player did WELL in 'strengths', not only mistakes."
        )
        d, model = None, s.stage3_model
        syn_max = 2500  # room for thinking models before the JSON
        est = _estimate_call(ctx.vision, s.stage3_model, len(syn) // 4 + 200, syn_max)
        if ctx.cost.remaining >= est:
            try:
                res = ctx.vision.generate(
                    model=s.stage3_model, prompt=syn, images_jpeg=[], schema=schema, max_tokens=syn_max
                )
                ctx.cost.charge(f"stage3-synth:{s.stage3_model}", _charge_call(ctx.vision, s.stage3_model, res))
                d, model = res.data, res.model
            except BudgetExceeded:
                d = None
        if d is None:  # no budget to synthesise — hand back the raw segment notes as the report
            ctx.match.warnings.append("Out of budget before synthesis; report is the raw segment notes.")
            d = {"summary": "Whole-match read (unsynthesised — budget reached).",
                 "recurring_mistakes": notes, "positioning_issues": [],
                 "decision_patterns": [], "practice_drills": []}
        self._emit_report(ctx, d, frames_reviewed=len(dense), segments=len(notes),
                          model=model, before=before)

    @staticmethod
    def _reconcile_score(ctx: PipelineContext, boards: list[tuple[int, int]], side: str) -> None:
        """Correct the reported score from what the model READ on the HUD.

        OCR of a tiny score region is noisy (we saw a real 4-1 read as 4-8). The
        vision model reading the full scoreboard is a strong second opinion; the
        end-of-match value is the trailing majority of its reads. We only override
        when there's genuine support and the two actually disagree — otherwise OCR
        stands."""
        if len(boards) < 2:
            return
        tail = boards[-max(3, len(boards) // 2):]
        vh, va = _majority([b[0] for b in tail]), _majority([b[1] for b in tail])
        if vh is None or va is None:
            return
        oh = ctx.match.outcome.get("score_home")
        oa = ctx.match.outcome.get("score_away")
        if (vh, va) == (oh, oa):
            return  # OCR and vision agree — nothing to do

        result = "draw" if vh == va else (
            "win" if (vh > va) == (side == "home") else "loss"
        )
        ctx.match.warnings.append(
            f"Score corrected from OCR {oh}-{oa} to {vh}-{va} using the model's on-screen "
            f"scoreboard reads (end-of-match consensus of {len(tail)} segments)."
        )
        ctx.match.outcome.update(
            {"score": f"{vh}-{va}", "score_home": vh, "score_away": va, "result": result,
             "score_source": "vision_corrected", "score_ocr": f"{oh}-{oa}"}
        )
        # Drop score-change events that are now impossible under the corrected score
        # (e.g. phantom concedes above the real away total).
        kept = []
        for e in ctx.match.events:
            if e.category == EventCategory.SCORE_CHANGE:
                team = e.payload.get("team")
                cap_v = vh if team == "home" else va
                if e.payload.get("score", 0) > cap_v:
                    continue
            kept.append(e)
        ctx.match.events = kept

    @staticmethod
    def _facts_preamble(match) -> str:
        side = match.capture.get("player_side", "unknown")
        score = match.outcome.get("score", "unknown")
        result = match.outcome.get("result", "unknown")
        goals = []
        for e in match.events:
            if e.category == EventCategory.SCORE_CHANGE:
                secs = e.timestamp_ms // 1000
                goals.append(f"{e.game_event_type} ~{secs // 60:02d}:{secs % 60:02d}")
        goals_txt = "; ".join(goals) if goals else "none read"
        return (
            "MATCH FACTS (from scoreboard OCR — trust over the images):\n"
            f"- the player is the '{side}' side\n"
            f"- final score: {score} ({result} for the player)\n"
            f"- scoring events: {goals_txt}"
        )

    @staticmethod
    def _select_frames(ctx: PipelineContext, max_frames: int):
        """Frames spread across the match + the moments near score changes."""
        live = sorted((f for f in ctx.frames if f.image is not None), key=lambda f: f.timestamp_ms)
        if not live:
            return []
        picked = {f.index: f for f in _evenly_sample_frames(live, max_frames)}
        for e in ctx.match.events:
            if e.category in _IMPORTANT:
                nearest = min(live, key=lambda f: abs(f.timestamp_ms - e.timestamp_ms))
                picked[nearest.index] = nearest
        return sorted(picked.values(), key=lambda f: f.timestamp_ms)[: max_frames + 6]


class HighlightClips(Stage):
    """Auto-clip each important moment into a short H.264 mp4 from the frames
    around it (local ffmpeg, $0). Stores each clip in object storage and records
    its key on the event as `payload['clip']`."""

    name = "highlight_clips"

    def enabled(self, ctx: PipelineContext) -> bool:
        return ctx.settings.enable_highlights and ctx.object_store is not None

    def run(self, ctx: PipelineContext) -> None:
        s = ctx.settings
        window_ms = int(s.highlight_window_s * 1000)
        prefix = frame_prefix(ctx.match.id) + "clips/"
        moments = [e for e in ctx.match.events if e.category in _IMPORTANT]
        made = 0
        for ev in moments:
            near = [
                f for f in ctx.frames
                if f.image is not None and abs(f.timestamp_ms - ev.timestamp_ms) <= window_ms
            ]
            near.sort(key=lambda f: f.timestamp_ms)
            if len(near) < 2:
                continue
            try:
                mp4 = self._assemble(near, s.highlight_fps)
            except Exception as exc:  # noqa: BLE001 - a bad clip shouldn't fail the match
                ctx.match.warnings.append(f"highlight clip failed for {ev.id}: {exc}")
                continue
            key = f"{prefix}{ev.id}.mp4"
            ctx.object_store.put(key, mp4, content_type="video/mp4")
            ev.payload["clip"] = key
            made += 1
        ctx.emit(self.name, "done", f"{made} highlight clips")

    @staticmethod
    def _assemble(frames, fps: int) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for i, f in enumerate(frames):
                cv2.imwrite(str(tmp_path / f"f{i:04d}.jpg"), f.image)
            out = tmp_path / "clip.mp4"
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-framerate", str(fps), "-i", str(tmp_path / "f%04d.jpg"),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                 str(out)],
                check=True,
            )
            return out.read_bytes()


class GeminiVideoCoaching(Stage):
    """Whole-match coaching from the RAW video (no frame extraction, no OCR).
    Selected when the match source is VIDEO_NATIVE.

    Two-pass for DEPTH (mirrors the frame map-reduce): (1) watch the video and
    emit a dense, chronological observation log + the score; (2) a stronger model
    synthesises that log into the deep coaching report. Single-pass fallback if
    two_pass is off. Returns the same coaching_report shape as the frame path."""

    name = "gemini_video_coaching"

    def enabled(self, ctx: PipelineContext) -> bool:
        return ctx.match.source_type == SourceType.VIDEO_NATIVE

    def run(self, ctx: PipelineContext) -> None:
        from core.ai.gemini_video import GeminiVideoModel

        s = ctx.settings
        if not ctx.source_bytes:
            ctx.match.warnings.append("No video uploaded for native analysis.")
            ctx.match.status = MatchStatus.FAILED
            ctx.emit(self.name, "failed", "no video")
            return
        if not s.openai_api_key:
            ctx.match.warnings.append("No Gemini key (OPENAI_API_KEY) set for native video analysis.")
            ctx.match.status = MatchStatus.FAILED
            ctx.emit(self.name, "failed", "no key")
            return

        adapter = ctx.adapter
        side = ctx.match.capture.get("player_side", "home")
        where = "top/left" if side == "home" else "bottom/right"
        lens = adapter.stage_prompt(3) or ""
        model = GeminiVideoModel(
            api_key=s.openai_api_key,
            in_usd_per_mtok=s.openai_input_usd_per_mtok,
            out_usd_per_mtok=s.openai_output_usd_per_mtok,
        )

        # SINGLE-CALL mode: one Gemini call over the whole video -> the report. No
        # multi-pass watch, no scoreboard/roster/deep-goal/self-learning extras — so
        # a match is ~1 request and can't trip the rate limit. Chosen via config.
        if not s.gemini_video_two_pass:
            self._run_single_call(ctx, model, adapter, side, lens)
            return

        # --- Pass 1: WATCH -> dense observation log + score --------------------
        srow = "TOP" if side == "home" else "BOTTOM"
        sbadge = "LEFT" if side == "home" else "RIGHT"
        obadge = "RIGHT" if side == "home" else "LEFT"
        observe_prompt = (
            f"Watch this ENTIRE match video. This is a human-vs-human match — BOTH teams have a "
            f"controlled-player arrow/gamertag, so do NOT use the arrow to pick the user. The user "
            f"is the '{side}' team; use the rules below to identify it and coach ONLY that team.\n"
            f"STEP 0 — IDENTIFY THE USER'S ('{side}') TEAM (get this RIGHT before anything else):\n"
            f"  - The scoreboard (top-left) lists two teams: HOME on the TOP row, AWAY on the "
            f"BOTTOM row. The user is '{side}', i.e. the {srow} row — note that team's 3-letter "
            f"abbreviation.\n"
            f"  - The bottom bar has TWO on-ball NAME badges: the LEFT badge is the HOME team's "
            f"player and the RIGHT badge is the AWAY team's player. So the USER'S player names come "
            f"ONLY from the {sbadge} badge. The {obadge} badge is the OPPONENT — NEVER use it or its "
            f"names for the user.\n"
            f"  - Identify the '{side}' team's KIT COLOUR (watch their goalkeeper, or their players "
            f"when the {sbadge} badge is active). Follow the team by KIT across half-time: at "
            f"half-time teams switch ENDS (attack the other way) but keep the SAME kit and the SAME "
            f"scoreboard row — never re-identify by pitch side.\n"
            f"  - Fill 'your_team' with kit (colour), abbrev, scoreboard_side ('{side}').\n\n"
            f"NAME the user's players (read them from the correct badge) — I want specific names. "
            f"Only if you truly cannot read a name, use the role ('your CB', 'your striker'). Never "
            f"invent a name and never use an opponent's name.\n\n"
            f"Produce a DENSE, chronological log of 20-35 CONCRETE, SPECIFIC observations about the "
            f"USER'S team across the whole match. Each observation MUST have 'time' (MM:SS you saw "
            f"it) and 'note' = WHO (named user player), WHAT they did, and WHERE (relative to the "
            f"user's own goal vs the opponent's goal — NOT left/right, since ends switch at "
            f"half-time). Cover build-up/passing, attacking movement and chances, defensive shape "
            f"and marking, transitions, and individual duels — BOTH good and bad, with mistakes. "
            f"When a mistake happens, note the DEFENSIVE/attacking action that was needed (e.g. a "
            f"manual switch to a specific player, a jockey, a clearance). Only report what you "
            f"ACTUALLY see; if you can't place a timestamp, skip it.\n"
            f"Also read the on-screen scoreboard for the FINAL score (home vs away).\n"
            f"Finally, list in 'knowledge_gaps' up to 5 FC 26-SPECIFIC things you saw but are NOT "
            f"certain about (a mechanic, animation, player role, UI element, or term) — phrase each "
            f"as a question to look up, e.g. 'What does the X role do in FC 26?'."
        )
        # Compress ONCE (small, fast upload) — big speed/cost win on large clips.
        if s.gemini_video_compress:
            ctx.emit(self.name, "compressing", "shrinking the video for a fast upload")
            video_bytes = _compress_video(ctx.source_bytes)
        else:
            video_bytes = ctx.source_bytes

        # Deterministic ROSTER read (OCR the name badges) — kicked off in parallel
        # with the upload/watch so it adds little wall-time. Gives the coach the REAL
        # squad per side, so it can't call an opponent one of "your" players.
        roster_future = None
        regions = adapter.name_badge_regions()
        # Roster OCR is LOCAL (PaddleOCR, no API) so it runs parallel with the watch
        # for free. The scoreboard timeline makes many Gemini calls, so it is NOT run
        # here — firing it alongside the watch saturates the rate limit and 429s the
        # watch. It runs AFTER the watch instead (below).
        if s.enable_roster_ocr and regions and ctx.ocr is not None and ctx.source_bytes:
            from concurrent.futures import ThreadPoolExecutor as _TPE
            from core.extraction.rosters import read_rosters
            _side_ex = _TPE(max_workers=1)
            # Read badges from the ORIGINAL (crisper text); frame extraction is a
            # cheap seek, not a full re-encode.
            roster_future = _side_ex.submit(
                read_rosters, ctx.source_bytes, ctx.ocr, regions,
                s.roster_sample_s, s.roster_max_frames,
            )

        # Upload ONCE, then run N viewings (self-consistency) IN PARALLEL against the
        # same file — so 2 viewings cost ~one upload + parallel reads.
        passes = max(1, s.gemini_video_watch_passes)
        pass_lists: list[list[str]] = []
        scores: list[tuple] = []
        all_gaps: list[str] = []
        your_team: dict = {}
        watch_cost = 0.0
        model_watch = s.gemini_video_model

        try:
            file_uri = model.prepare(
                video_bytes, on_step=lambda st, d: ctx.emit(self.name, st, d))
        except Exception as exc:  # noqa: BLE001 - upload failed (e.g. Gemini 503)
            ctx.match.warnings.append(f"native video analysis failed: {exc}")
            ctx.match.status = MatchStatus.FAILED
            ctx.emit(self.name, "failed", str(exc))
            return

        def _watch(_k):
            return model.analyze_uri(
                file_uri, model=s.gemini_video_model, prompt=observe_prompt,
                schema=_VIDEO_OBSERVE_SCHEMA, media_resolution=s.gemini_video_media_res,
                max_tokens=8000,  # thinking models spend tokens reasoning first
            )

        ctx.emit(self.name, "running",
                 f"watching video x{passes} on {s.gemini_video_model} ({s.gemini_video_media_res} res)")
        results: list = [None] * passes
        errors: list = []
        if passes == 1:
            try:
                results[0] = _watch(0)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=passes) as ex:
                futs = {ex.submit(_watch, k): k for k in range(passes)}
                for fut in futs:
                    k = futs[fut]
                    try:
                        results[k] = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        errors.append(exc)

        if not any(r is not None for r in results):  # every viewing failed (e.g. Gemini 503)
            msg = str(errors[0]) if errors else "no result"
            ctx.match.warnings.append(f"native video analysis failed: {msg}")
            ctx.match.status = MatchStatus.FAILED
            ctx.emit(self.name, "failed", msg)
            return
        for exc in errors:
            ctx.match.warnings.append(f"a watch pass failed: {exc}")

        # Process successful viewings sequentially (cost + state mutations on the main thread).
        for obs_res in results:
            if obs_res is None:
                continue
            try:
                ctx.cost.charge(f"video-watch:{s.gemini_video_model}", obs_res.cost_usd)
            except BudgetExceeded:
                ctx.match.warnings.append("Video watch cost over the cap; using viewings gathered so far.")
                break
            watch_cost += obs_res.cost_usd
            od = obs_res.data or {}
            pass_lists.append(_format_observations(od.get("observations") or []))
            sc = od.get("score") or {}
            scores.append((_plausible_score(sc.get("home")), _plausible_score(sc.get("away"))))
            all_gaps += [str(g) for g in (od.get("knowledge_gaps") or []) if g]
            your_team = your_team or (od.get("your_team") or {})
            model_watch = obs_res.model

        # Consensus: merge across viewings (support>=2 = corroborated); score by majority.
        if len(pass_lists) > 1:
            merged = _merge_passes(pass_lists)
            observations = [f"[{m['secs'] // 60:02d}:{m['secs'] % 60:02d}] {m['note']}" for m in merged]
            supports = [m["support"] for m in merged]
            corroborated = sum(1 for x in supports if x >= 2)
            ctx.emit(self.name, "consensus",
                     f"{len(pass_lists)} viewings -> {corroborated}/{len(observations)} observations corroborated")
        else:
            observations = pass_lists[0] if pass_lists else []
            supports = [1] * len(observations)
        vh = _majority([h for h, _ in scores if h is not None])
        va = _majority([a for _, a in scores if a is not None])
        self._apply_score(ctx, {"home": vh, "away": va}, side)

        if not observations:
            ctx.match.warnings.append(
                "The model returned no usable observations (likely a truncated response); "
                "no report produced. Try again."
            )
            ctx.match.status = MatchStatus.FAILED
            ctx.emit(self.name, "failed", "no observations")
            return

        # Collect the OCR'd rosters + the deterministic scoreboard score (both
        # started in parallel with the watch).
        rosters: dict = {}
        if roster_future is not None:
            try:
                rosters = roster_future.result(timeout=240) or {}
            except Exception as exc:  # noqa: BLE001
                ctx.match.warnings.append(f"roster OCR failed: {exc}")
        if roster_future is not None:
            _side_ex.shutdown(wait=False)

        # Deterministic FINAL SCORE + GOAL TIMELINE from hi-res scoreboard crops —
        # run NOW (after the watch) so its many small calls don't starve the watch's
        # rate limit. The model hallucinates the score/goal-count in long matches;
        # watching the score change gives exact goal times + sides.
        det_goals: list[dict] = []  # deterministic goal timeline (time + which side)
        score_cost = 0.0
        score_granularity = 0.0
        if ctx.source_bytes and s.gemini_score_read:
            ctx.emit(self.name, "scoring", "reading the scoreboard timeline")
            try:
                timeline = _read_score_timeline(ctx.source_bytes, s)
                if timeline and timeline.get("final"):
                    sb_score = timeline["final"]
                    score_cost = timeline.get("cost_usd", 0.0) or 0.0
                    score_granularity = timeline.get("granularity_s", 0.0) or 0.0
                    self._apply_score(ctx, sb_score, side)
                    ctx.match.outcome["score_source"] = "scoreboard_read"
                    # Tag each goal scored/conceded from the player's perspective.
                    for g in timeline.get("goals", []):
                        det_goals.append({
                            "secs": g["secs"], "time": g["time"],
                            "type": "scored" if g["side"] == side else "conceded",
                        })
                    ctx.emit(self.name, "score",
                             f"scoreboard: {sb_score['home']}-{sb_score['away']} "
                             f"({len(det_goals)} goals timed)")
            except Exception as exc:  # noqa: BLE001
                ctx.match.warnings.append(f"scoreboard score read failed: {exc}")
        # Charge the (previously uncounted) scoreboard reads so the total is honest.
        if score_cost:
            self._charge_soft(ctx, f"scoreboard-read:{s.stage2_model}", score_cost)
        my_roster = rosters.get(side, [])
        opp_roster = rosters.get("away" if side == "home" else "home", [])
        if my_roster:
            ctx.emit(self.name, "roster", f"read your squad: {', '.join(my_roster[:6])}")

        # --- DEEP GOAL RE-READ: now that we know WHEN each goal happened, re-watch
        # the seconds around the most important conceded ones with the strong model.
        deep_cost = 0.0
        if s.gemini_deep_goals and det_goals:
            ctx.emit(self.name, "deep_goals",
                     f"deep-reading key goals on {s.gemini_video_synth_model}")
            det_goals, deep_cost = _deep_read_goals(
                video_bytes, s, det_goals, side, my_roster, opp_roster,
                granularity_s=score_granularity)
            if deep_cost:
                self._charge_soft(ctx, f"deep-goals:{s.gemini_video_synth_model}", deep_cost)
            deep_n = sum(1 for g in det_goals if g.get("deep"))
            ctx.emit(self.name, "deep_goals", f"analysed {deep_n} key goals in depth")

        # --- COACH: deep synthesis on a stronger model -------------------------
        d, model_used, synth_cost = {}, model_watch, 0.0
        if s.gemini_video_two_pass and observations:
            facts = self._score_facts(ctx.match, side)
            playbook = adapter.coaching_playbook(" ".join(observations))
            history = _history_block(ctx.player_history, adapter.issue_vocabulary())
            # Player context (control scheme + skill level) -> calibrate inputs/depth.
            control = str(ctx.match.capture.get("control_scheme", "")).strip()
            skill = str(ctx.match.capture.get("skill_level", "")).strip()
            player_ctx = ""
            if control:
                player_ctx += (f"The player uses {control} controls — give button/stick inputs for "
                               f"{control} where schemes differ. ")
            if skill:
                deep = skill.lower() in ("pro", "competitive", "elite")
                player_ctx += (f"Skill level: {skill} — "
                               + ("skip fundamentals; go deep, meta-aware and advanced."
                                  if deep else "explain the fundamentals clearly and simply."))
            if player_ctx:
                player_ctx = "PLAYER CONTEXT: " + player_ctx + "\n\n"

            roster_block = ""
            if my_roster:
                roster_block = (
                    f"YOUR SQUAD (read directly from the scoreboard badges) is ONLY these players: "
                    f"{', '.join(my_roster)}. "
                    + (f"The OPPONENT's players include: {', '.join(opp_roster)}. " if opp_roster else "")
                    + "Attribute actions to YOUR players by name. If a name is NOT in your squad "
                    "list, that player is the OPPONENT — refer to your own man by ROLE ('your CB'), "
                    "and NEVER credit an opponent's action to you.\n\n"
                )
            # Deterministic goal log (read from the scoreboard) — when we have it,
            # the goal COUNT/TIME/SIDE are FACTS; the model only writes the colour.
            if det_goals:
                log = "; ".join(
                    f"G{i + 1} [{g['time']}] {g['type']}" for i, g in enumerate(det_goals)
                )
                goals_instr = (
                    f"GOALS (AUTHORITATIVE — read from the scoreboard, do not question): there were "
                    f"EXACTLY {len(det_goals)} goals, in this order: {log}. Return 'goals' with EXACTLY "
                    f"these {len(det_goals)} entries IN THIS ORDER; copy each entry's 'time' and 'type' "
                    f"VERBATIM (do not add, drop, reorder or re-time any). For each, write 'summary' "
                    f"(what happened + which of YOUR players was involved, from the observations nearest "
                    f"that time) and, for 'conceded' goals, a 'fix'. If no observation covers a goal, give "
                    f"a brief general summary — but keep the time and type exactly.\n"
                )
            else:
                goals_instr = (
                    f"GOALS: the FINAL SCORE is authoritative — the 'goals' list must total EXACTLY "
                    f"(home + away) goals and NO MORE. Do NOT infer a goal from an attacking move or a "
                    f"chance; only count actual goals consistent with the scoreline. Each entry: time "
                    f"(MM:SS), type ('scored'|'conceded'), summary (what happened + which player), and "
                    f"for conceded goals a 'fix'. Order by time.\n"
                )
            multi = len(pass_lists) > 1
            numbered = "\n".join(
                f"{i + 1}. {'★ ' if supports[i] >= 2 else ''}{o}" for i, o in enumerate(observations)
            )
            corr_note = (
                "Observations marked ★ were seen in MULTIPLE independent viewings of the match "
                "(reliable). Unmarked ones were seen only once (low confidence). For any claim "
                "about a SPECIFIC incident (a goal, a specific mistake) rely on ★ observations; "
                "do NOT build a key claim on an unmarked observation alone.\n\n" if multi else ""
            )
            syn = (
                f"{facts}\n\n{player_ctx}{roster_block}{playbook}\n\n{history}\n\nA coach watched the full match "
                f"and logged these NUMBERED, time-stamped observations about the '{side}' player:\n\n"
                f"{numbered}\n\n{corr_note}{lens}\n\n"
                f"Synthesise ONE deep coaching report. Talk DIRECTLY to the player as 'you', like a "
                f"coach — for each point say what you did, WHY it helped or hurt, and exactly what to "
                f"do instead. Find RECURRING patterns, be BALANCED (real strengths AND weaknesses), "
                f"4-6 items per list.\n"
                f"NAME the user's players (use the names in the observations, e.g. 'Cancelo drifted "
                f"inside'). CONTROLS: for every mistake/weakness, prescribe the EXACT FC 26 input the "
                f"user should have used at that moment, drawn from the knowledge above — e.g. "
                f"'manual-switch (Right Stick flick) to your CB before their through-ball', 'jockey "
                f"with L2/LT instead of lunging', 'clear with the shoot button (not a pass)'. Be "
                f"specific about the button/stick, not vague.\n"
                f"COACHING METHOD: diagnose the recurring PATTERN, give its ROOT CAUSE (why it cost "
                f"you), lead with the single BIGGEST fix first, and where the OPPONENT exploited you "
                f"name the specific COUNTER. Reference a meta principle, a formation/role tweak, or "
                f"the PRO REFERENCE from the knowledge above when it fits.\n"
                f"ERROR TYPE: classify each mistake and PREFIX it — '[Mechanical]' = wrong INPUT for "
                f"the situation (fix = the exact button/stick to press, practised in isolation) or "
                f"'[Decision]' = right input but wrong READ (fix = the positioning/game-sense "
                f"reasoning, not a button). The fix must match the type.\n"
                f"META CAVEAT: any formation/meta/'current-meta' advice may go stale after a patch — "
                f"mark it '(meta — verify post-patch)'.\n"
                f"GROUNDING RULE: base every point ONLY on the observations above. For each item set "
                f"'evidence_ids' = the numbers of the observations it is based on (e.g. [4, 12]). Do "
                f"NOT claim anything not supported by an observation. Practice-drill items may have "
                f"empty evidence_ids.\n"
                f"Also set 'weakness_tags' = the 2-5 tags from the WEAKNESS TAGS list that best match "
                f"this player's weaknesses this match (exact tag strings only). And set 'score' = the "
                f"FINAL score {{home, away}} you read from the scoreboard/observations.\n"
                f"{goals_instr}"
                f"STATS: set 'stats' with integer counts you observed — shots, big_chances, goals_for, "
                f"goals_against, goals_conceded_from_crosses, defensive_errors. "
                f"FORMATION: set 'formation' = the user's shape if you can tell (e.g. '4-2-3-1'), else ''."
            )
            ctx.emit(self.name, "synthesising", f"deep coaching write-up on {s.gemini_video_synth_model}")
            try:
                syn_res = model.generate_text(
                    model=s.gemini_video_synth_model, prompt=syn,
                    # Big room: thinking model + a richer report (5 sections + goals +
                    # stats + tags) can truncate at a low cap.
                    schema=_video_report_schema(), max_tokens=16000,
                )
                if syn_res.data.get("summary"):
                    # Map cited observation ids -> real timestamps (deterministic).
                    d = _flatten_report(syn_res.data, observations)
                    d["weakness_tags"] = [str(t) for t in (syn_res.data.get("weakness_tags") or [])]
                    d["goals"] = _reconcile_goals(det_goals, syn_res.data.get("goals") or [])
                    d["stats"] = syn_res.data.get("stats") or {}
                    d["formation"] = str(syn_res.data.get("formation") or "")
                    # Score fallback: if the watch pass didn't read a score, use the
                    # synthesis's (it reliably knows the final scoreline).
                    if not ctx.match.outcome.get("score"):
                        self._apply_score(ctx, syn_res.data.get("score") or {}, side)
                    model_used, synth_cost = syn_res.model, syn_res.cost_usd
                else:
                    ctx.match.warnings.append(
                        "synthesis returned no summary (likely truncated) — using raw observation log."
                    )
            except Exception as exc:  # noqa: BLE001
                ctx.match.warnings.append(f"video synthesis failed, using raw log: {exc}")
            if synth_cost:
                try:
                    ctx.cost.charge(f"video-synth:{s.gemini_video_synth_model}", synth_cost)
                except BudgetExceeded:
                    ctx.match.warnings.append(f"Video synthesis cost ${synth_cost:.4f}, over the cap.")

        # Fallback: if synthesis didn't run, surface the observation log itself.
        if not d.get("summary") and observations:
            d = {"summary": "Whole-match video read (observation log).",
                 "recurring_mistakes": observations[:8], "strengths": [],
                 "positioning_issues": [], "decision_patterns": [], "practice_drills": []}
        # The scoreboard goal timeline is deterministic — keep it even when the
        # synthesis failed to fill in the descriptions.
        if det_goals and not d.get("goals"):
            d["goals"] = _reconcile_goals(det_goals, [])

        ctx.match.insights = [
            Insight(
                scope="match", kind="coaching_report", summary=str(d.get("summary", "")),
                payload={
                    "strengths": d.get("strengths", []),
                    "recurring_mistakes": d.get("recurring_mistakes", []),
                    "positioning_issues": d.get("positioning_issues", []),
                    "decision_patterns": d.get("decision_patterns", []),
                    "practice_drills": d.get("practice_drills", []),
                    "player_side": side, "analysis": "gemini_video",
                    "your_team": your_team,
                    "roster": my_roster,
                    "opponent_roster": opp_roster,
                    "weakness_tags": d.get("weakness_tags", []),
                    "goals": d.get("goals", []),
                    "stats": d.get("stats", {}),
                    "formation": d.get("formation", ""),
                    "history_matches": (ctx.player_history or {}).get("matches", 0),
                    "regular_squad": (ctx.player_history or {}).get("squad", []),
                    "usual_formation": (ctx.player_history or {}).get("formation", ""),
                    "observations_count": len(observations),
                    "corroborated_count": sum(1 for x in supports if x >= 2),
                    "viewings": len(pass_lists),
                    "evidence_log": observations[:30],
                },
                model=model_used,
                # Honest cost of THIS report: every model call that fed it —
                # watch + synthesis + scoreboard/goal-timeline reads + deep goal reads.
                cost_usd=round(watch_cost + synth_cost + score_cost + deep_cost, 6),
            )
        ]
        _stats_to_metrics(ctx, d.get("stats", {}))
        ctx.emit(self.name, "done", f"video coaching ready, cost=${ctx.cost.total:.4f}")

        # --- Self-learning: queue the model's knowledge gaps and research a few --
        gaps = list(dict.fromkeys(all_gaps))  # dedupe, preserve order, across viewings
        if s.enable_self_learning and gaps:
            self._learn(ctx, model, gaps)

    def _learn(self, ctx: PipelineContext, model, gaps: list[str]) -> None:
        """Queue newly-seen FC 26 unknowns and research up to N via Google-Search
        grounding, filing sourced facts into the brain (learned.yaml)."""
        try:
            from adapters.ea_fc_26 import knowledge_base as kb
        except Exception:
            return
        kb.queue_gaps(gaps, match_id=ctx.match.id)
        s = ctx.settings
        to_learn = kb.open_gaps(limit=s.learn_max_gaps_per_run)
        learned = 0
        for g in to_learn:
            q = (f"In EA Sports FC 26 (current title update), {g['question']} "
                 f"Answer in 1-3 concise factual sentences; if it is not specifically "
                 f"a real FC 26 thing, reply exactly 'unknown'.")
            try:
                r = model.research(model=ctx.settings.gemini_video_model, question=q)
            except Exception as exc:  # noqa: BLE001
                ctx.match.warnings.append(f"self-learning research failed: {exc}")
                break
            try:
                ctx.cost.charge("self-learn:research", r.get("cost_usd", 0.0))
            except BudgetExceeded:
                break
            ans = (r.get("answer") or "").strip()
            if ans and ans.lower() != "unknown" and len(ans) > 15:
                kb.resolve_gap(g["id"], ans, r.get("sources", []))
                learned += 1
        if learned:
            ctx.emit(self.name, "learned", f"researched {learned} new FC 26 fact(s) into the brain")

    def _run_single_call(self, ctx: PipelineContext, model, adapter, side: str, lens: str) -> None:
        """One Gemini call over the whole video -> the coaching report. Minimal
        requests (compress locally, upload once, ONE generate) so it never trips the
        rate limit. No scoreboard/roster/deep/self-learning extras."""
        s = ctx.settings
        srow = "TOP" if side == "home" else "BOTTOM"
        sbadge = "LEFT" if side == "home" else "RIGHT"
        obadge = "RIGHT" if side == "home" else "LEFT"
        playbook = adapter.coaching_playbook("")
        history = _history_block(ctx.player_history, adapter.issue_vocabulary())
        control = str(ctx.match.capture.get("control_scheme", "")).strip()
        skill = str(ctx.match.capture.get("skill_level", "")).strip()
        player_ctx = ""
        if control:
            player_ctx += f"The player uses {control} controls — give inputs for {control}. "
        if skill:
            deep = skill.lower() in ("pro", "competitive", "elite")
            player_ctx += (f"Skill level: {skill} — "
                           + ("go deep, meta-aware." if deep else "explain fundamentals simply."))

        prompt = (
            f"You are an elite EA Sports FC 26 coach. Watch this ENTIRE match video and coach the "
            f"'{side}' team (the user). This is human-vs-human, so do NOT use the controlled-player "
            f"arrow to pick the user.\n"
            f"IDENTIFY THE USER'S TEAM FIRST: the scoreboard (top-left) shows HOME on the TOP row and "
            f"AWAY on the BOTTOM — the user is '{side}', the {srow} row. The bottom bar's {sbadge} "
            f"on-ball name badge is the USER'S player; the {obadge} badge is the OPPONENT — never "
            f"credit the opponent's actions to the user. Follow the user's team by KIT colour across "
            f"half-time (ends switch, kit + scoreboard row stay).\n\n"
            f"{player_ctx}\n\n{playbook}\n\n{history}\n\n{lens}\n\n"
            f"Write ONE deep coaching report, talking DIRECTLY to the player as 'you'. For each point: "
            f"what you did, WHY it helped/hurt, and the EXACT FC 26 input or positioning to use instead. "
            f"Find RECURRING patterns, be BALANCED (real strengths AND weaknesses), 4-6 items per list. "
            f"NAME the user's players (read from the {sbadge} badge); never invent names. "
            f"Prefix each mistake '[Mechanical]' (wrong input) or '[Decision]' (wrong read). Mark any "
            f"meta/formation advice '(meta — verify post-patch)'.\n"
            f"TIMESTAMPS: END every item in strengths, recurring_mistakes, positioning_issues and "
            f"decision_patterns with the VIDEO timestamp(s) where you saw it, in parentheses — e.g. "
            f"'... jockey instead. (03:12)' or '... (01:40, 06:05)'. This MUST be the elapsed position "
            f"in THIS video clip (time from the start of the clip), NOT the in-game match clock shown on "
            f"the scoreboard. The clip is only a few minutes long, so every timestamp must be within the "
            f"clip's real duration. This lets the player jump to the exact moment — every point needs at least one.\n"
            f"Read the on-screen scoreboard for the FINAL score and set 'score' {{home, away}}. The "
            f"'goals' list must total EXACTLY (home+away) goals — one entry each: time (MM:SS), type "
            f"('scored'|'conceded'), summary (what happened + which player), and a 'fix' for conceded "
            f"ones. Set 'stats' (integer counts you saw), 'formation' if identifiable, and "
            f"'weakness_tags' (2-5 from the tag list above)."
        )

        if s.gemini_video_compress:
            ctx.emit(self.name, "compressing", "shrinking the video for a fast upload")
            video_bytes = _compress_video(ctx.source_bytes)
        else:
            video_bytes = ctx.source_bytes

        ctx.emit(self.name, "running", f"single-call coaching read on {s.gemini_video_model}")
        # The file is uploaded once (prepare); retry the generate on an empty/truncated
        # response so a paid call never fails for nothing.
        res, d, uri = None, {}, None
        for attempt in range(2):
            try:
                if uri is None:
                    uri = model.prepare(video_bytes, on_step=lambda st, dd: ctx.emit(self.name, st, dd))
                res = model.analyze_uri(
                    uri, model=s.gemini_video_model, prompt=prompt,
                    schema=_lite_report_schema(), media_resolution=s.gemini_video_media_res,
                    max_tokens=20000,
                )
            except Exception as exc:  # noqa: BLE001
                if attempt == 1:
                    ctx.match.warnings.append(f"native video analysis failed: {exc}")
                    ctx.match.status = MatchStatus.FAILED
                    ctx.emit(self.name, "failed", str(exc))
                    return
                continue
            d = res.data or {}
            if d.get("summary"):
                break
            ctx.emit(self.name, "retry", "empty response — retrying the coaching read")

        if not d.get("summary"):
            ctx.match.warnings.append("single-call report returned no summary (truncated?). Try again.")
            ctx.match.status = MatchStatus.FAILED
            ctx.emit(self.name, "failed", "no report")
            return
        self._charge_soft(ctx, f"video-single:{res.model}", res.cost_usd)
        self._apply_score(ctx, d.get("score") or {}, side)

        # Drop any coaching-point timestamps that fall outside the clip (the model
        # occasionally reports the in-game match clock) so 'jump to moment' is safe.
        dur = _video_duration(video_bytes)
        for _k in ("strengths", "recurring_mistakes", "positioning_issues", "decision_patterns"):
            if isinstance(d.get(_k), list):
                d[_k] = _clamp_point_times([str(x) for x in d[_k]], dur)

        # MIDDLE MODE: add the lightweight deterministic scoreboard timeline for an
        # accurate final score + timed goal-by-goal, without the deep per-goal reads.
        goals = d.get("goals", [])
        score_cost = 0.0
        if s.gemini_score_read and ctx.source_bytes:
            ctx.emit(self.name, "scoring", "reading the scoreboard timeline")
            try:
                timeline = _read_score_timeline(ctx.source_bytes, s)
                if timeline and timeline.get("final"):
                    sb = timeline["final"]
                    score_cost = timeline.get("cost_usd", 0.0) or 0.0
                    self._apply_score(ctx, sb, side)
                    ctx.match.outcome["score_source"] = "scoreboard_read"
                    det_goals = [{
                        "secs": g["secs"], "time": g["time"],
                        "type": "scored" if g["side"] == side else "conceded",
                    } for g in timeline.get("goals", [])]
                    goals = _reconcile_goals(det_goals, d.get("goals", []))
                    self._charge_soft(ctx, f"scoreboard-read:{s.stage2_model}", score_cost)
                    ctx.emit(self.name, "score",
                             f"scoreboard: {sb['home']}-{sb['away']} ({len(det_goals)} goals timed)")
            except Exception as exc:  # noqa: BLE001
                ctx.match.warnings.append(f"scoreboard score read failed: {exc}")

        ctx.match.insights = [
            Insight(
                scope="match", kind="coaching_report", summary=str(d.get("summary", "")),
                payload={
                    "strengths": d.get("strengths", []),
                    "recurring_mistakes": d.get("recurring_mistakes", []),
                    "positioning_issues": d.get("positioning_issues", []),
                    "decision_patterns": d.get("decision_patterns", []),
                    "practice_drills": d.get("practice_drills", []),
                    "player_side": side, "analysis": "gemini_video_single",
                    "weakness_tags": [str(t) for t in (d.get("weakness_tags") or [])],
                    "goals": goals,
                    "stats": d.get("stats", {}),
                    "formation": str(d.get("formation") or ""),
                    "history_matches": (ctx.player_history or {}).get("matches", 0),
                },
                model=res.model, cost_usd=round(res.cost_usd + score_cost, 6),
            )
        ]
        _stats_to_metrics(ctx, d.get("stats", {}))
        ctx.emit(self.name, "done", f"coaching ready, cost=${ctx.cost.total:.4f}")

    @staticmethod
    def _charge_soft(ctx: PipelineContext, label: str, usd: float) -> None:
        """Record already-spent money so the reported total is honest, even if it
        pushes over the cap (unlike a pre-flight charge, this can't be halted — the
        API call already happened). Falls back to a direct append on BudgetExceeded."""
        try:
            ctx.cost.charge(label, usd)
        except BudgetExceeded:
            from core.pipeline.cost import Charge
            ctx.cost.charges.append(Charge(label=label, usd=usd))
            ctx.match.warnings.append(f"{label} ${usd:.4f} pushed the match over its budget cap.")

    @staticmethod
    def _apply_score(ctx: PipelineContext, sc: dict, side: str) -> None:
        vh, va = _plausible_score(sc.get("home")), _plausible_score(sc.get("away"))
        if vh is not None and va is not None:
            result = "draw" if vh == va else ("win" if (vh > va) == (side == "home") else "loss")
            ctx.match.outcome.update(
                {"score": f"{vh}-{va}", "score_home": vh, "score_away": va,
                 "result": result, "score_source": "gemini_video"}
            )

    @staticmethod
    def _score_facts(match, side: str) -> str:
        o = match.outcome
        if "score" not in o:
            return f"MATCH FACTS: the player is the '{side}' side."
        return (f"MATCH FACTS (trust these): the player is the '{side}' side; final score "
                f"{o.get('score')} ({o.get('result')} for the player).")


# Pass-1 (watch) output: a TIMESTAMPED observation log + the scoreboard read + any
# FC 26 things the model saw but is unsure about (fuel for self-learning). Each
# observation carries the video time it was seen so coaching can cite evidence.
_VIDEO_OBSERVE_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"time": {"type": "string"}, "note": {"type": "string"}},
                "required": ["time", "note"],
            },
        },
        "score": {"type": "object",
                  "properties": {"home": {"type": "integer"}, "away": {"type": "integer"}}},
        "your_team": {"type": "object", "properties": {
            "kit": {"type": "string"}, "abbrev": {"type": "string"},
            "scoreboard_side": {"type": "string"}}},
        "knowledge_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["observations"],
}


DEFAULT_STAGES: list[Stage] = [
    Stage1LocalExtraction(),
    Stage2CheapEvents(),
    Stage3CoachingReport(),
    HighlightClips(),
]

# Native whole-video path (Gemini) — one stage, no frame extraction.
VIDEO_NATIVE_STAGES: list[Stage] = [GeminiVideoCoaching()]
