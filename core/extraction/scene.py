"""Scene detection via frame differencing. €0, no model.

Two jobs in Phase 0:
  1. Find scene changes (goal cutaways, replays, menus, the stat screen).
  2. Find STATIC RUNS - stretches where consecutive frames barely change. A
     full-time / match-facts screen holds still, so a long static run is the
     prime candidate for "this is the stat screen".
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from core.extraction.frames import Frame


def _signature(image_bgr: np.ndarray) -> np.ndarray:
    """A tiny grayscale thumbnail; comparing these is a cheap frame-diff."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (32, 18), interpolation=cv2.INTER_AREA)
    return small.astype(np.float32)


def _diff(a: np.ndarray, b: np.ndarray) -> float:
    """Normalised mean absolute difference in [0, 1]."""
    return float(np.mean(np.abs(a - b)) / 255.0)


@dataclass
class SceneAnalysis:
    # Per-frame diff vs the previous frame (frame 0 = 0.0).
    diffs: list[float] = field(default_factory=list)
    # Frame indices where a scene change was detected.
    scene_changes: list[int] = field(default_factory=list)
    # Runs of near-identical frames as (start_index, end_index_inclusive).
    static_runs: list[tuple[int, int]] = field(default_factory=list)

    def longest_static_runs(self, min_len: int) -> list[tuple[int, int]]:
        return [r for r in self.static_runs if (r[1] - r[0] + 1) >= min_len]


class SceneDetector:
    def __init__(self, change_threshold: float = 0.08, static_threshold: float = 0.02) -> None:
        # diff above change_threshold => scene change; below static_threshold => "held still".
        self.change_threshold = change_threshold
        self.static_threshold = static_threshold

    def analyze(self, frames: list[Frame]) -> SceneAnalysis:
        analysis = SceneAnalysis()
        sigs = [_signature(f.image) for f in frames if f.image is not None]
        if not sigs:
            return analysis

        analysis.diffs.append(0.0)
        for i in range(1, len(sigs)):
            d = _diff(sigs[i - 1], sigs[i])
            analysis.diffs.append(d)
            if d >= self.change_threshold:
                analysis.scene_changes.append(i)

        # Collect maximal runs of consecutive "held still" frames.
        run_start = 0
        for i in range(1, len(sigs) + 1):
            is_static = i < len(sigs) and analysis.diffs[i] <= self.static_threshold
            if not is_static:
                if i - 1 > run_start:  # run of length >= 2
                    analysis.static_runs.append((run_start, i - 1))
                run_start = i
        return analysis
