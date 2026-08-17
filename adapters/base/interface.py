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
    franchise: str               # "ea-fc" - annual editions share a franchise
    edition: str                 # "26" - adapters are versioned PER EDITION
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


class ReportSpec(BaseModel):
    """How one game shapes a whole-match coaching report.

    The CORE owns the envelope - summary, strengths, recurring mistakes, weakness
    tags, the final score, and the evidence-citation mechanism - because those are
    coaching concepts, not football ones. Everything in here is the game's own
    vocabulary and lives with its adapter, so adding a game never edits /core.

    This started life as `_report_template_props()`, `_TEMPLATE_INSTRUCTIONS`,
    `_TEMPLATE_KEYS`, `_STAT_METRICS` and `_DEEP_GOAL_SCHEMA` inside
    core/pipeline/stages.py, which put half-spaces, cutbacks and centre-backs in
    the game-agnostic core.
    """

    # JSON-schema properties for the game-specific report sections, e.g.
    # {"attacking": {...}, "defending": {...}}. Their keys are also what gets
    # carried into the stored payload - there is no second list to keep in sync.
    sections: dict[str, Any] = Field(default_factory=dict)

    # Prompt text telling the model how to fill `sections`.
    instructions: str = ""

    # Observed stat key -> (label, higher_is_better). Drives BOTH the integer
    # properties in the report schema and the Metrics written for trends, so the
    # two cannot list different stats.
    stats: dict[str, tuple[str, bool]] = Field(default_factory=dict)

    # Schema for re-watching a single scoring play in depth (a goal here, a round
    # elsewhere). None means the game has no such second pass.
    score_event: dict[str, Any] | None = None

    # The flat "label: sentence" sections, as (section key, heading, [(field key,
    # human label)...]), in document order. Renderers walk this instead of keeping
    # their own copy of the field labels - core/report/pdf.py used to carry a
    # duplicate table, which meant a field added to the schema was answered by the
    # model, stored by the API, shown on the web, and silently missing from the
    # PDF. Only the flat sections: list-shaped ones have bespoke rendering.
    kv_sections: list[tuple[str, str, list[tuple[str, str]]]] = Field(default_factory=list)

    def stats_schema(self) -> dict[str, Any]:
        """`stats` as JSON-schema properties. Counts only - see the note in the
        adapter about why the qualitative fields are strings."""
        return {"type": "object",
                "properties": {k: {"type": "integer"} for k in self.stats}}


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

    def skill_survey(self) -> list[dict]:
        """Optional questions whose answers HINT at how good the player is, so the
        app can suggest a coaching level instead of asking them to self-assess.

        Game-specific by nature (competitive ladders differ per title), which is
        why it lives here and not in core. Shape:
            [{"key": str, "label": str, "help": str,
              "options": [{"value": str, "label": str}],
              "locked_by": {"key": str, "values": [str], "reason": str}}]
        `locked_by` disables this question while another answer is in that set.
        """
        return []

    def suggest_skill_level(self, answers: dict) -> dict | None:
        """Map survey answers to a SUGGESTED skill level.

        Returns {"level": str, "reason": str} or None when there is not enough to
        go on. It is only ever a suggestion - the player always chooses.
        """
        return None

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

    def prompt_fragments(self, side: str = "home") -> dict[str, str]:
        """The game's own WORDS in the model prompts, keyed by a role the core fixes.

        The core owns the SHAPE of every prompt - what to return, how to cite
        evidence, that timestamps are clip-relative, that an empty list is a real
        answer. It must not own sentences like "home is the TOP row", "jockey
        with L2/LT" or "switch to your left winger": those are as specific to one
        game as a schema field is, and they used to sit in /core next to the rule
        that no game may appear there.

        Values are templates; the core formats in what it knows ({n} images,
        {time}, {question}). Anything derived from `side` is baked in here, so the
        core never learns that home is the top row.

        A missing key yields "" - the surrounding instructions are written to
        stand on their own, so a game supplies as much or as little as it has.
        """
        return {}

    def report_spec(self) -> ReportSpec:
        """The game-specific shape of a coaching report. Default is empty: a game
        that declares nothing still gets the core envelope, just no sections of
        its own."""
        return ReportSpec()

    def coaching_schema(self) -> dict[str, Any]:
        """Structured schema for a whole-match coaching report (Stage 3). One
        report per match - recurring mistakes, positioning, decisions, drills."""
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

    # Convenience derived from identity - the core routes on these.
    @property
    def key(self) -> str:
        ident = self.identity()
        return f"{ident.game_id}@{ident.edition}"
