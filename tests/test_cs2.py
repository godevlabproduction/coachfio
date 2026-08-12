"""Phase 2: a deliberately different second game (CS2, replay/stats source, no
video) must drop in as a pure /adapters plugin and flow through the same core."""
from pathlib import Path

from adapters.base.registry import load_builtin_adapters, registry
from adapters.cs2.adapter import Cs2Adapter
from core.config import Settings
from core.extraction.ocr import StubOcrEngine
from core.models.domain import Match
from core.models.enums import EventCategory, SourceType
from core.pipeline.context import PipelineContext
from core.pipeline.cost import CostAccountant
from core.pipeline.runner import run_pipeline

_FIXTURE = (Path(__file__).parent / "fixtures" / "cs2_match.json").read_bytes()


def test_cs2_registered_by_edition():
    load_builtin_adapters()
    ad = registry.get("cs2", "2")
    ident = ad.identity()
    assert ident.display_name == "Counter-Strike 2"
    assert SourceType.REPLAY_FILE in ident.supported_sources
    # A replay game has no HUD schema but still resolves one (empty).
    assert ad.hud_schema().regions == []


def test_cs2_ingest_parses_replay():
    parsed = Cs2Adapter().ingest(_FIXTURE)
    assert parsed.outcome["score"] == "13-11"
    assert parsed.outcome["result"] == "win"
    keyed = {m.key: m.value for m in parsed.metrics}
    assert keyed["score_home"] == 13.0
    assert keyed["kills_home"] == 85.0
    # 4 round_win events + the highlights (ace, clutch, bomb_planted).
    round_wins = [e for e in parsed.events if e.game_event_type == "round_win"]
    highlights = [e for e in parsed.events if e.category == EventCategory.HIGHLIGHT]
    assert len(round_wins) == 4
    assert {e.game_event_type for e in highlights} == {"ace", "clutch", "bomb_planted"}


def test_replay_source_runs_through_core_pipeline():
    """The SAME run_pipeline that handles FC video handles a CS2 replay — it
    dispatches on SourceType, with no game-specific branching in core."""
    load_builtin_adapters()
    match = Match(game_id="cs2", game_edition="2", source_type=SourceType.REPLAY_FILE)
    ctx = PipelineContext(
        match=match,
        adapter=registry.get("cs2", "2"),
        frames=[],                       # no video frames for a replay source
        ocr=StubOcrEngine(),
        settings=Settings(),
        cost=CostAccountant(cap_usd=0.25),
        source_bytes=_FIXTURE,
    )
    run_pipeline(ctx)
    assert match.status.value == "complete"
    assert match.outcome["score"] == "13-11"
    assert match.cost_usd == 0.0
    assert any(e.game_event_type == "round_win" for e in match.events)
