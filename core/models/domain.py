"""The four things the core understands. Deliberately game-agnostic.

    Match   - a bounded session with a start, end, and outcome
    Event   - a timestamped thing that happened
    Metric  - a number extracted from the match
    Insight - a pattern found across many matches

Adapters produce these from raw input; the dashboard, progress tracking,
billing, and storage consume them and never change when a game is added.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from core.models.enums import EventCategory, MatchStatus, MetricSource, SourceType


def _uuid() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Metric(BaseModel):
    """A single number extracted from a match.

    `key` is stable and game-defined (e.g. "possession_pct"); the core treats
    it opaquely. `higher_is_better` lets the game-agnostic progress layer decide
    whether a trend is improvement without knowing what the metric means.
    """

    key: str
    label: str
    value: float
    unit: str | None = None
    higher_is_better: bool | None = None
    source: MetricSource = MetricSource.OCR
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    # Free-form provenance (region name, raw OCR text, frame index…). JSONB.
    extra: dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    """A timestamped occurrence, normalized into a core `category`.

    `game_event_type` keeps the adapter's original word for display/debugging;
    the core only ever branches on `category`.
    """

    id: str = Field(default_factory=_uuid)
    timestamp_ms: int  # offset from match start
    category: EventCategory = EventCategory.UNKNOWN
    game_event_type: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    # Which extracted frame(s) support this event (object-store keys).
    frame_refs: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class Insight(BaseModel):
    """A pattern, usually cross-match. Populated in Phase 1+ (Stage 3). Present
    here so the schema is stable from day one."""

    id: str = Field(default_factory=_uuid)
    scope: str = "match"  # "match" | "player" | "trend"
    kind: str = "note"
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=_now)


class Match(BaseModel):
    """A bounded session. Game identity is a plain id + edition - the core never
    interprets it, it just carries it so the right adapter can be looked up."""

    id: str = Field(default_factory=_uuid)

    # Adapter routing - opaque to the core.
    game_id: str
    game_edition: str
    adapter_version: str | None = None

    source_type: SourceType = SourceType.VIDEO
    status: MatchStatus = MatchStatus.CREATED

    # Optional client-declared capture info (resolution etc.) that helps pick a
    # HUD schema variant. Opaque to the core.
    capture: dict[str, Any] = Field(default_factory=dict)

    # Results
    outcome: dict[str, Any] = Field(default_factory=dict)  # e.g. {"result":"win","score":"3-1"}
    metrics: list[Metric] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    insights: list[Insight] = Field(default_factory=list)

    # Bookkeeping
    cost_usd: float = 0.0
    parse_confidence: float | None = None  # drop => alert (patch may have moved the HUD)
    warnings: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    ended_at: datetime | None = None

    def metric(self, key: str) -> Metric | None:
        return next((m for m in self.metrics if m.key == key), None)
