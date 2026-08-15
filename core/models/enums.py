"""Core vocabularies. These are game-AGNOSTIC on purpose.

An adapter maps its own event vocabulary (e.g. "goal", "yellow_card") onto
`EventCategory`. The core never sees game words; it only ever sees these
categories. If you feel the urge to add a football word here, it belongs in an
adapter instead.
"""
from __future__ import annotations

from enum import Enum


class SourceType(str, Enum):
    """How raw data reaches us. The pipeline prefers the cheapest source an
    adapter supports that satisfies the request."""

    VIDEO = "video"          # high cost (vision) - frames extracted + OCR pipeline
    VIDEO_NATIVE = "video_native"  # whole video sent to a video-capable model (Gemini)
    REPLAY_FILE = "replay"   # near zero
    PUBLIC_API = "api"       # near zero
    SCREENSHOT = "screenshot"  # very low


class MatchStatus(str, Enum):
    CREATED = "created"          # match row exists, awaiting frames
    UPLOADING = "uploading"      # client is uploading extracted frames
    QUEUED = "queued"            # frames in, job enqueued
    PROCESSING = "processing"    # pipeline running
    COMPLETE = "complete"
    FAILED = "failed"
    OVER_BUDGET = "over_budget"  # halted: would exceed the per-match cost cap


class EventCategory(str, Enum):
    """The only event kinds the core understands. Adapters translate into these."""

    SCORE_CHANGE = "score_change"        # the scoreline moved (goal, point, kill…)
    PERIOD_BOUNDARY = "period_boundary"  # half/quarter/round start or end
    STAT_SNAPSHOT = "stat_snapshot"      # a stats/summary screen was read
    DISCIPLINE = "discipline"            # card, foul, penalty - a sanction
    ROSTER_CHANGE = "roster_change"      # substitution, swap
    SCENE_CHANGE = "scene_change"        # replay / cutscene / menu boundary
    HIGHLIGHT = "highlight"              # a moment flagged as important
    UNKNOWN = "unknown"


class SkillLevel(str, Enum):
    """How experienced the player is. Game-agnostic: it describes the PERSON, not
    the game, and it decides how a coaching report is pitched - vocabulary,
    assumed knowledge, and how much is explained rather than asserted.

    An amateur cannot act on advice written for a pro; a pro is insulted by
    advice written for an amateur. Same footage, different report.
    """

    AMATEUR = "amateur"
    INTERMEDIATE = "intermediate"
    PRO = "pro"

    @classmethod
    def parse(cls, value: object, default: "SkillLevel | None" = None) -> "SkillLevel":
        """Best-effort parse. Accepts the canonical values plus the older free-text
        labels the upload form used to send ("Casual", "Competitive")."""
        raw = str(value or "").strip().lower()
        aliases = {
            "casual": cls.AMATEUR, "beginner": cls.AMATEUR, "new": cls.AMATEUR,
            "amateur": cls.AMATEUR,
            "intermediate": cls.INTERMEDIATE, "competitive": cls.INTERMEDIATE,
            "average": cls.INTERMEDIATE, "regular": cls.INTERMEDIATE,
            "pro": cls.PRO, "advanced": cls.PRO, "elite": cls.PRO, "expert": cls.PRO,
        }
        return aliases.get(raw, default or cls.INTERMEDIATE)


class MetricSource(str, Enum):
    OCR = "ocr"        # read off the HUD locally (Stage 1, €0)
    MODEL = "model"    # inferred by a vision model (Stage 2/3)
    DERIVED = "derived"  # computed from other metrics/events
