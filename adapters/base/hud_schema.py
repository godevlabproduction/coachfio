"""HUD schema types.

A HUD schema is *data*, not code. It describes WHERE on screen the score,
clock, and stats live and HOW to parse them. Adding a game is (mostly) writing
one of these YAML files.

Coordinates are NORMALIZED to [0, 1] fractions of frame width/height, not
pixels. FC 26's HUD sits at the same relative position at 1080p, 1440p, and 4K,
so one schema scales across resolutions — we only re-calibrate when a game
*patch* moves the HUD, which is what `schema_version` is for.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SceneKind(str, Enum):
    """Which kind of frame a region is expected to be read from."""

    IN_MATCH = "in_match"        # live play: scoreboard + clock visible
    STAT_SCREEN = "stat_screen"  # half/full-time summary with the numbers
    ANY = "any"


class ContentType(str, Enum):
    DIGITS = "digits"    # 0-9 only
    CLOCK = "clock"      # MM:SS
    SCORE = "score"      # single team score integer
    PERCENT = "percent"  # 0-100, may have a trailing %
    TEXT = "text"        # free text (team name, "FULL TIME")


class HudRegion(BaseModel):
    """One rectangle to OCR, with just enough info to parse what's inside."""

    name: str
    # Normalized [x, y, w, h], each in [0, 1]. Origin top-left.
    rect: tuple[float, float, float, float]
    content_type: ContentType = ContentType.TEXT
    scene: SceneKind = SceneKind.ANY
    # Optional character whitelist to constrain OCR (e.g. "0123456789:").
    whitelist: str | None = None
    # Optional regex the parsed text should satisfy; failure lowers confidence.
    pattern: str | None = None
    # If True, absence/low-confidence of this region drops match parse_confidence.
    required: bool = False
    # Free-form hints for the adapter's interpret() (e.g. {"team": "home"}).
    meta: dict[str, Any] = Field(default_factory=dict)

    def pixel_box(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Return (x0, y0, x1, y1) in pixels for a frame of this size."""
        x, y, w, h = self.rect
        x0 = max(0, int(round(x * width)))
        y0 = max(0, int(round(y * height)))
        x1 = min(width, int(round((x + w) * width)))
        y1 = min(height, int(round((y + h) * height)))
        return x0, y0, x1, y1


class StatScreenSignature(BaseModel):
    """How to recognise a stat/summary screen among the extracted frames.

    Kept declarative: a region that should contain one of `anchor_text`, plus a
    'this frame is mostly static' expectation handled by the scene detector.
    """

    anchor_region: str | None = None          # region name expected to hold a title
    anchor_text: list[str] = Field(default_factory=list)  # e.g. ["FULL TIME","MATCH FACTS"]
    min_static_frames: int = 3                 # summary screens hold still


class HudSchema(BaseModel):
    game_id: str
    edition: str
    # Bump when a game patch moves the HUD. Parsing confidence dropping is the
    # signal that a live schema has gone stale.
    schema_version: str = "1"
    # Documents what the normalized coords were measured against.
    reference_resolution: tuple[int, int] = (1920, 1080)
    regions: list[HudRegion] = Field(default_factory=list)
    stat_screen: StatScreenSignature = Field(default_factory=StatScreenSignature)

    def regions_for(self, scene: SceneKind) -> list[HudRegion]:
        return [
            r for r in self.regions
            if r.scene == scene or r.scene == SceneKind.ANY or scene == SceneKind.ANY
        ]

    def region(self, name: str) -> HudRegion | None:
        return next((r for r in self.regions if r.name == name), None)
