"""Everything a pipeline run needs, in one bag. Game-agnostic: the adapter is
injected as an opaque `GameAdapter`; the pipeline never names a game."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from core.ai.vision import VisionModel
from core.config import Settings
from core.extraction.frames import Frame
from core.extraction.ocr import OcrEngine
from core.models.domain import Match
from core.pipeline.cost import CostAccountant
from core.storage.objectstore import ObjectStore

if TYPE_CHECKING:
    # Type-only import keeps the runtime dependency direction core -/-> adapters.
    from adapters.base.interface import GameAdapter


class ProgressReporter(Protocol):
    def report(self, stage: str, status: str, detail: str = "", **extra) -> None: ...


class NullReporter:
    def report(self, stage: str, status: str, detail: str = "", **extra) -> None:  # noqa: D401
        return None


@dataclass
class PipelineContext:
    match: Match
    adapter: "GameAdapter"
    frames: list[Frame]
    ocr: OcrEngine
    settings: Settings
    cost: CostAccountant
    reporter: ProgressReporter = field(default_factory=NullReporter)
    vision: VisionModel | None = None
    object_store: ObjectStore | None = None
    # Raw bytes for a non-video source (replay file / API payload). Video matches
    # use `frames` instead; replay/API matches use this.
    source_bytes: bytes | None = None

    # Candidate frame indices flagged by Stage 1 (scene changes, stat screens).
    # Stage 2 classifies these with the cheap model; keeps game logic out of core.
    candidate_frames: list[int] = field(default_factory=list)
    stat_frames: list[int] = field(default_factory=list)

    # This player's recurring issues across past matches (the "learns you" loop):
    # {"matches": N, "issues": [{"tag","label","count","last"}...], "recent_advice": [...]}
    player_history: dict | None = None

    def frame_by_index(self, index: int) -> Frame | None:
        return next((f for f in self.frames if f.index == index), None)

    def emit(self, stage: str, status: str, detail: str = "", **extra) -> None:
        self.reporter.report(stage, status, detail, **extra)
