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

    VIDEO = "video"          # high cost (vision) — frames extracted + OCR pipeline
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
    DISCIPLINE = "discipline"            # card, foul, penalty — a sanction
    ROSTER_CHANGE = "roster_change"      # substitution, swap
    SCENE_CHANGE = "scene_change"        # replay / cutscene / menu boundary
    HIGHLIGHT = "highlight"              # a moment flagged as important
    UNKNOWN = "unknown"


class MetricSource(str, Enum):
    OCR = "ocr"        # read off the HUD locally (Stage 1, €0)
    MODEL = "model"    # inferred by a vision model (Stage 2/3)
    DERIVED = "derived"  # computed from other metrics/events
