"""The contract every game implements.

The pipeline only ever talks to a `GameAdapter` through this interface. It hands
the adapter raw OCR readings and gets back core objects (Metric/Event/outcome).
The pipeline stays game-agnostic; all football knowledge lives behind here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from core.models.enums import EventCategory, SourceType
from core.models.domain import Event, Metric
from adapters.base.hud_schema import HudRegion, HudSchema


class GameIdentity(BaseModel):
    game_id: str                 # stable, e.g. "ea-fc"
    display_name: str            # "EA Sports FC 26"
    franchise: str               # "ea-fc" — annual editions share a franchise
    edition: str                 # "26" — adapters are versioned PER EDITION
    platforms: list[str] = Field(default_factory=list)   # ["ps5","xbox","pc"]
    supported_sources: list[SourceType] = Field(default_factory=lambda: [SourceType.VIDEO])


class MetricDefinition(BaseModel):
    key: str
    label: str
    unit: str | None = None
    higher_is_better: bool | None = None
    # Where the number comes from, declaratively:
    #   "ocr:<region_name>"  read directly off a HUD region
    #   "derived"            computed by the adapter's interpret()
    source_expr: str = "derived"


class EventTypeDef(BaseModel):
    """One entry of a game's event vocabulary, mapped to a core category."""

    game_type: str               # adapter's word, e.g. "goal"
    category: EventCategory      # core category it maps to
    description: str = ""


class RegionReading(BaseModel):
    """One OCR result for one region on one frame. Produced by the HudReader,
    consumed by the adapter's interpret()."""

    region: HudRegion
    frame_index: int
    timestamp_ms: int
    text: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_stat_screen: bool = False


class ParsedHud(BaseModel):
    """What an adapter returns after interpreting a match's readings."""

    outcome: dict[str, Any] = Field(default_factory=dict)
    metrics: list[Metric] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    parse_confidence: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class GameAdapter(ABC):
    """Implement this (usually by subclassing ConfigDrivenAdapter) to add a game."""

    @abstractmethod
    def identity(self) -> GameIdentity: ...

    @abstractmethod
    def hud_schema(self, capture: dict[str, Any] | None = None) -> HudSchema:
        """Return the HUD schema, optionally choosing a variant from capture
        info (platform/resolution). Data-driven wherever possible."""

    @abstractmethod
    def event_vocabulary(self) -> list[EventTypeDef]: ...

    @abstractmethod
    def metric_definitions(self) -> list[MetricDefinition]: ...

    @abstractmethod
    def stage_prompt(self, stage: int) -> str:
        """Prompt template for Stage 2 (event pass) or Stage 3 (deep read).
        Unused in Phase 0 but part of the contract."""

    def coaching_playbook(self, hints: str = "") -> str:
        """Game-specific knowledge grounding for the coaching model (Layer 1
        'brain'). Default: none. `hints` (e.g. the observation log) lets the
        adapter surface the most relevant entries."""
        return ""

    def issue_vocabulary(self) -> list[dict]:
        """Controlled weakness tags [{tag,label}] so the coach labels each match's
        weaknesses consistently and the longitudinal loop can aggregate them."""
        return []

    def name_badge_regions(self) -> dict | None:
        """Normalized (x,y,w,h) regions of the on-ball NAME badges, keyed by
        scoreboard side: {"home": rect, "away": rect}. Used to OCR the actual
        roster so coaching can't misattribute players. None = not supported."""
        return None

    def ingest(self, source: bytes) -> ParsedHud:
        """Non-video ingestion: parse a replay file or API payload directly into
        metrics/events/outcome. Video adapters use hud_schema()+interpret()
        instead and leave this unimplemented; replay/API adapters implement this
        and leave interpret() unused. The pipeline dispatches on the match's
        SourceType (a core concept), never on the game."""
        raise NotImplementedError(f"{type(self).__name__} has no non-video ingest()")

    @abstractmethod
    def interpret(self, readings: list[RegionReading]) -> ParsedHud:
        """THE ~10% of code: turn raw region readings into metrics, an outcome,
        and events. All game logic lives here."""

    def validate(self, parsed: ParsedHud) -> list[str]:
        """Sanity checks (e.g. a football score can't decrease). Returns
        warnings. Default: none."""
        return []

    # --- Stage 2 / 3 structured-output schemas ------------------------------
    # Defaults are built from the adapter's own vocabulary, so the pipeline can
    # request structured output generically without knowing any game words.

    def stage2_label_schema(self) -> dict[str, Any]:
        """JSON schema the small model must fill: one label from this game's
        vocabulary (plus 'in_play' for ordinary play) + a confidence."""
        labels = [e.game_type for e in self.event_vocabulary()] + ["in_play"]
        return {
            "type": "object",
            "properties": {
                "label": {"type": "string", "enum": labels},
                "confidence": {"type": "number"},
            },
            "required": ["label", "confidence"],
            "additionalProperties": False,
        }

    def insight_schema(self) -> dict[str, Any]:
        """JSON schema for a Stage 3 deep-read insight. Generic by default;
        an adapter may override for game-specific structure."""
        return {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "summary": {"type": "string"},
                "factors": {"type": "array", "items": {"type": "string"}},
                "coaching_point": {"type": "string"},
            },
            "required": ["kind", "summary"],
            "additionalProperties": False,
        }

    def event_type_map(self) -> dict[str, EventTypeDef]:
        """game_type -> EventTypeDef, for mapping a Stage 2 label to a core event."""
        return {e.game_type: e for e in self.event_vocabulary()}

    def coaching_schema(self) -> dict[str, Any]:
        """Structured schema for a whole-match coaching report (Stage 3). One
        report per match — recurring mistakes, positioning, decisions, drills."""
        _arr = {"type": "array", "items": {"type": "string"}}
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "strengths": _arr,
                "recurring_mistakes": _arr,
                "positioning_issues": _arr,
                "decision_patterns": _arr,
                "practice_drills": _arr,
            },
            "required": [
                "summary", "strengths", "recurring_mistakes", "positioning_issues",
                "decision_patterns", "practice_drills",
            ],
            "additionalProperties": False,
        }

    # Convenience derived from identity — the core routes on these.
    @property
    def key(self) -> str:
        ident = self.identity()
        return f"{ident.game_id}@{ident.edition}"
