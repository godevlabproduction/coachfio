"""Deterministic ROSTER reader for the broadcast overlay.

The wrong-player problem (the coach naming an opponent as "your" player) comes
from asking a general model to decide whose player is whose. Instead we OCR the
two on-ball NAME badges at their fixed positions across many frames: the LEFT
badge is the HOME team's player, the RIGHT badge is the AWAY team's. Aggregating
names per side builds the ACTUAL roster, which we then feed to the coach as a hard
constraint ("your players are ONLY these; anyone else is the opponent").

Game-specific region coordinates come from the adapter, not core.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import cv2

_NAME = re.compile(r"[A-Z][A-Z'\-]{2,}")
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ'-"


def _extract_frames(video_bytes: bytes, every_s: float, max_frames: int,
                    start_s: float = 0.0) -> list:
    """Sample one frame every `every_s` seconds (via ffmpeg), up to `max_frames`.

    `start_s` seeks before decoding (input-side `-ss`, so it is a fast keyframe
    seek) - used to re-read a narrow window at finer granularity without paying to
    decode the whole video again.
    """
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        vid = p / "m.mp4"
        vid.write_bytes(video_bytes)
        seek = ["-ss", f"{start_s:.2f}"] if start_s > 0 else []
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *seek, "-i", str(vid),
             "-vf", f"fps=1/{every_s}", "-frames:v", str(max_frames), "-q:v", "3",
             str(p / "f%04d.jpg")],
            check=True,
        )
        for f in sorted(p.glob("f*.jpg")):
            img = cv2.imread(str(f))
            if img is not None:
                frames.append(img)
    return frames


def _crop(img, rect: tuple):
    h, w = img.shape[:2]
    x, y, cw, ch = rect
    x0, y0 = max(0, int(x * w)), max(0, int(y * h))
    x1, y1 = min(w, int((x + cw) * w)), min(h, int((y + ch) * h))
    return img[y0:y1, x0:x1]


def _name_from(text: str) -> str | None:
    hits = _NAME.findall((text or "").upper())
    return max(hits, key=len) if hits else None


def read_rosters(video_bytes: bytes, ocr, regions: dict, every_s: float = 5.0,
                 max_frames: int = 60, min_count: int = 2) -> dict:
    """Return {"home": [names...], "away": [names...]} read from the badge regions.
    `regions` = {"home": (x,y,w,h), "away": (x,y,w,h)} normalized [0-1]."""
    frames = _extract_frames(video_bytes, every_s, max_frames)
    counts = {"home": Counter(), "away": Counter()}
    for img in frames:
        for side in ("home", "away"):
            rect = regions.get(side)
            if not rect:
                continue
            crop = _crop(img, rect)
            if crop is None or crop.size == 0:
                continue
            crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            try:
                text, conf = ocr.read_text(crop, _LETTERS)
            except Exception:  # noqa: BLE001 - a bad frame shouldn't fail the match
                continue
            if conf is not None and conf < 0.45:
                continue
            nm = _name_from(text)
            if nm:
                counts[side][nm] += 1
    return {
        side: [n for n, c in counts[side].most_common() if c >= min_count]
        for side in ("home", "away")
    }
