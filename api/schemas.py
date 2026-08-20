from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from core.models.enums import SourceType


class CreateMatchRequest(BaseModel):
    game_id: str = "ea-fc"
    edition: str = "26"
    source_type: SourceType = SourceType.VIDEO
    # Client-declared capture info: resolution, platform, fps. Helps pick a HUD
    # schema variant. Opaque to the core.
    capture: dict[str, Any] = Field(default_factory=dict)


class CreateMatchResponse(BaseModel):
    match_id: str
    status: str
    # Client uploads frames to POST {frames_endpoint} as multipart, then calls
    # {complete_endpoint}.
    frames_endpoint: str
    complete_endpoint: str
    progress_endpoint: str


class GameInfo(BaseModel):
    game_id: str
    edition: str
    display_name: str
    franchise: str
    platforms: list[str]
    supported_sources: list[str]
    metric_keys: list[str]


class FrameUploadResponse(BaseModel):
    match_id: str
    index: int
    key: str


class TrendResponse(BaseModel):
    key: str
    label: str
    unit: str | None
    higher_is_better: bool | None
    latest: float | None
    previous: float | None
    delta: float | None
    improving: bool | None
    average: float | None
    # Your normal for this metric, from the WHOLE history rather than the
    # selected window - so "last 5" compares recent form against how you usually
    # play, instead of against itself. None until there are enough matches for it
    # to mean anything.
    baseline: float | None = None
    baseline_n: int = 0
    # Where the number came from. "model" means the coach counted it while
    # watching, with no ground truth to check it against; goals are read off the
    # scoreboard instead. The UI marks the difference so an estimate does not
    # look as solid as a measurement.
    source: str | None = None
    estimated: bool = False
    points: list[dict[str, Any]]


class ReportFeedbackRequest(BaseModel):
    """A verdict on one report, filled in after reading it.

    `section` is which part let them down and `note` is what was wrong with it.
    The note is the half that matters: "bad" is not actionable, "you said I dive
    in but I was switching to press" is, and that sentence is what the model is
    shown next time.
    """

    rating: int                # 1-5
    section: str = ""          # e.g. "defending" - free-form; sections are the adapter's
    note: str = ""
