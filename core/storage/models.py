"""SQLAlchemy tables.

Schema-evolution strategy: volatile / game-specific shapes live in JSONB
(`outcome`, `capture`, `payload`, `extra`, `warnings`), so an adapter can add
fields without a migration. Only the stable, queryable columns are typed - and
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
    # Stage 3 insights (coaching) - match-scoped JSON blob (low volume).
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


class UserRow(Base):
    """An account. `user_id` is whatever the auth seam (api/deps.current_user)
    returns - a dev header today, a hosted provider's subject claim later - so
    swapping in Clerk/Auth0/Supabase needs no change here.

    Holds only coaching preferences and display fields. NO credentials: password
    hashing and sessions belong to the auth provider, per the project brief.
    """

    __tablename__ = "users"

    # OUR id, and deliberately not the auth provider's. It is the primary key
    # here and it is also written into every match (capture->>'identity'), so
    # adopting a provider's subject as this value would marry the match history
    # to that vendor: switching later would mean rewriting JSONB on every row.
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    # The provider's subject claim ("sub"), once one is connected. Nullable
    # because accounts exist that predate it, and unique because two accounts
    # must never map to the same login. Changing provider means re-populating
    # this one column - no match data moves.
    auth_subject: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True)

    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Coaching calibration. Applied to every new match; overridable per upload.
    skill_level: Mapped[str] = mapped_column(String(32), default="intermediate")
    control_scheme: Mapped[str] = mapped_column(String(32), default="Classic")

    # Free-form answers to the adapter's skill survey (e.g. FC's Division Rivals
    # tier and Champs record). Opaque here on purpose: the questions are
    # game-specific and live in the adapter, so this must not become typed columns.
    skill_survey: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Public-facing coach profile: {headline, bio, specialties[], experience}.
    # JSONB rather than typed columns because this is presentation copy that will
    # keep changing shape, and none of it is queried.
    coach_profile: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Object-store key for an uploaded profile picture, or empty. Optional by
    # design: an account never needs one, and the UI falls back to initials.
    avatar: Mapped[str] = mapped_column(String(200), default="")

    # "player" | "coach". Decides what the app EMPHASISES (office vs locker,
    # first/third-person reports) - never how a report reads; that stays with
    # skill_level, because a coach can be new to the game and a pro player wants
    # dense output. Keep the two axes separate.
    role: Mapped[str] = mapped_column(String(16), default="player")

    # Practice-plan checkboard: {item_key: {"done": bool, "ts": iso}}. JSONB on
    # the user rather than a table - items are derived from the latest report's
    # practice plan, so only the tick state needs storing.
    checklist: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class CoachLinkRow(Base):
    """A player <-> coach connection. Always created by the PLAYER (choosing a
    coach from the directory) - that act is the consent that lets the coach read
    the player's progress and chat with them. Coaches cannot create links, so a
    coach account can never grant itself access to someone's data."""

    __tablename__ = "coach_links"

    coach_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    # "pending" until the coach accepts. NOTHING is granted while pending - not
    # chat, not the progress summary. The player asking is a request, not an act
    # that gives them a channel to someone who never agreed to coach them.
    status: Mapped[str] = mapped_column(String(16), default="pending")

    # Reports are a SEPARATE, opt-in grant on top of the link. Connecting shares
    # the progress summary; it does not hand over the full AI report, which names
    # players and reads like a private diary of someone's mistakes. Defaults to
    # False so the stronger permission is never granted implicitly.
    share_reports: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MessageRow(Base):
    """One chat message between two linked accounts. Plain rows + polling - no
    websockets until someone actually needs them."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sender: Mapped[str] = mapped_column(String(128), index=True)
    recipient: Mapped[str] = mapped_column(String(128), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
