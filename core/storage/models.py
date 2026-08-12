"""SQLAlchemy tables.

Schema-evolution strategy: volatile / game-specific shapes live in JSONB
(`outcome`, `capture`, `payload`, `extra`, `warnings`), so an adapter can add
fields without a migration. Only the stable, queryable columns are typed — and
metrics get their own row per (match, key) so trend queries stay simple SQL.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.storage.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MatchRow(Base):
    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    game_id: Mapped[str] = mapped_column(String(64), index=True)
    game_edition: Mapped[str] = mapped_column(String(32), index=True)
    adapter_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)

    parse_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # Evolvable / game-specific shapes.
    capture: Mapped[dict] = mapped_column(JSONB, default=dict)
    outcome: Mapped[dict] = mapped_column(JSONB, default=dict)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    # Stage 3 insights (coaching) — match-scoped JSON blob (low volume).
    insights: Mapped[list] = mapped_column(JSONB, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    metrics: Mapped[list["MetricRow"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
    events: Mapped[list["EventRow"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )


class MetricRow(Base):
    __tablename__ = "match_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(128))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    higher_is_better: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="ocr")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)

    match: Mapped[MatchRow] = relationship(back_populates="metrics")


class UsageRow(Base):
    """Per-identity usage counter for plan limits (matches analysed). Identity is
    supplied by the auth seam (api/deps.current_user); a hosted auth provider
    plugs in there without changing this table."""

    __tablename__ = "usage_counters"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    matches_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class EventRow(Base):
    __tablename__ = "match_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    game_event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    frame_refs: Mapped[list] = mapped_column(JSONB, default=list)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    match: Mapped[MatchRow] = relationship(back_populates="events")
