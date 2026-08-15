"""HudReader - applies a HUD schema's regions to frames and OCRs them.

This is where the schema (data) meets the pixels. It:
  1. Uses scene analysis to decide which frames are the stat/summary screen
     (long static run whose anchor region reads one of the schema's anchor texts).
  2. OCRs the stat-screen regions on the best representative frame.
  3. OCRs the in-match regions (score/clock) on sampled live frames.

Output is a flat list of `RegionReading`. The adapter's interpret() turns those
into metrics/events. The reader knows nothing about football.
"""
from __future__ import annotations

import numpy as np

from adapters.base.hud_schema import HudSchema, SceneKind
from adapters.base.interface import RegionReading
from core.extraction.frames import Frame
from core.extraction.ocr import OcrEngine
from core.extraction.scene import SceneAnalysis


def _crop(image: np.ndarray, region, width: int, height: int) -> np.ndarray:
    x0, y0, x1, y1 = region.pixel_box(width, height)
    if x1 <= x0 or y1 <= y0:
        return image[0:0, 0:0]
    return image[y0:y1, x0:x1]


class HudReader:
    def __init__(self, ocr: OcrEngine, in_match_sample_every: int = 1) -> None:
        self.ocr = ocr
        self.in_match_sample_every = max(1, in_match_sample_every)

    def _detect_stat_screens(
        self, frames: list[Frame], schema: HudSchema, scene: SceneAnalysis
    ) -> list[int]:
        """Return frame indices judged to be the stat/summary screen."""
        sig = schema.stat_screen
        anchor = schema.region(sig.anchor_region) if sig.anchor_region else None
        chosen: list[int] = []
        for start, end in scene.longest_static_runs(sig.min_static_frames):
            mid = (start + end) // 2
            if mid >= len(frames) or frames[mid].image is None:
                continue
            frame = frames[mid]
            w, h = frame.size
            if anchor and sig.anchor_text:
                crop = _crop(frame.image, anchor, w, h)
                text, _ = self.ocr.read_text(crop, anchor.whitelist)
                up = text.upper()
                if not any(a.upper() in up for a in sig.anchor_text):
                    continue  # static but not the stat screen (e.g. a paused replay)
            chosen.append(mid)
        return chosen

    def read(
        self, frames: list[Frame], schema: HudSchema, scene: SceneAnalysis
    ) -> tuple[list[RegionReading], list[int]]:
        readings: list[RegionReading] = []
        stat_frames = set(self._detect_stat_screens(frames, schema, scene))

        # --- stat-screen regions -------------------------------------------
        stat_regions = schema.regions_for(SceneKind.STAT_SCREEN)
        for idx in stat_frames:
            frame = frames[idx]
            w, h = frame.size
            for region in stat_regions:
                text, conf = self.ocr.read_text(_crop(frame.image, region, w, h), region.whitelist)
                readings.append(
                    RegionReading(
                        region=region,
                        frame_index=frame.index,
                        timestamp_ms=frame.timestamp_ms,
                        text=text,
                        confidence=conf,
                        is_stat_screen=True,
                    )
                )

        # --- in-match regions (sampled live frames) ------------------------
        in_match_regions = schema.regions_for(SceneKind.IN_MATCH)
        for i, frame in enumerate(frames):
            if frame.index in stat_frames or frame.image is None:
                continue
            if i % self.in_match_sample_every != 0:
                continue
            w, h = frame.size
            for region in in_match_regions:
                text, conf = self.ocr.read_text(_crop(frame.image, region, w, h), region.whitelist)
                if not text:
                    continue
                readings.append(
                    RegionReading(
                        region=region,
                        frame_index=frame.index,
                        timestamp_ms=frame.timestamp_ms,
                        text=text,
                        confidence=conf,
                        is_stat_screen=False,
                    )
                )
        return readings, sorted(stat_frames)
