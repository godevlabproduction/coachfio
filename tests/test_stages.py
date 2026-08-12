"""Stage 2 / Stage 3 with a scripted (offline) vision model.

Covers: candidate classification -> events (with the right exclusions), deep-read
-> insights, and the budget guard halting a stage before it overspends.
"""
import json

import numpy as np

from core.ai.vision import VisionResult
from core.config import Settings
from core.extraction.frames import Frame
from core.extraction.ocr import StubOcrEngine
from core.models.domain import Event, Match
from core.models.enums import EventCategory, SourceType
from core.pipeline.context import PipelineContext
from core.pipeline.cost import CostAccountant
from core.pipeline.stages import HighlightClips, Stage2CheapEvents, Stage3CoachingReport
from adapters.ea_fc_26.adapter import EaFc26Adapter


class ScriptedVision:
    """Returns a queued label/insight per call; reports fixed token usage."""

    def __init__(self, responder, in_tok=50, out_tok=10):
        self.responder = responder
        self.calls = 0
        self.in_tok, self.out_tok = in_tok, out_tok

    def generate(self, model, prompt, images_jpeg, schema=None, max_tokens=512):
        self.calls += 1
        data = self.responder(self.calls, schema)
        return VisionResult(
            data=data, text=json.dumps(data),
            input_tokens=self.in_tok, output_tokens=self.out_tok, model=model,
        )


def _frame(i):
    return Frame(index=i, timestamp_ms=i * 1000, key=f"k{i}", image=np.zeros((36, 64, 3), np.uint8))


def _ctx(vision, *, cap=0.25, stage2=True, stage3=True, events=None):
    settings = Settings(
        enable_stage_2=stage2, enable_stage_3=stage3,
        stage2_model="claude-haiku-4-5", stage3_model="claude-sonnet-5",
    )
    match = Match(game_id="ea-fc", game_edition="26")
    if events:
        match.events = events
    return PipelineContext(
        match=match, adapter=EaFc26Adapter(),
        frames=[_frame(i) for i in range(3)],
        ocr=StubOcrEngine(), settings=settings,
        cost=CostAccountant(cap_usd=cap), vision=vision,
        candidate_frames=[0, 1, 2],
    )


def test_stage2_adds_nonscore_events_only():
    # call 1: a card (kept), 2: in_play (ignored), 3: goal (score_change -> skipped;
    # OCR is authoritative for the score).
    labels = {1: "yellow_card", 2: "in_play", 3: "goal"}
    vision = ScriptedVision(lambda n, schema: {"label": labels[n], "confidence": 0.9})
    ctx = _ctx(vision)
    Stage2CheapEvents().run(ctx)

    kinds = [(e.category, e.game_event_type) for e in ctx.match.events]
    assert (EventCategory.DISCIPLINE, "yellow_card") in kinds
    assert all(gt != "goal" for _, gt in kinds)          # score_change excluded
    assert all(gt != "in_play" for _, gt in kinds)       # ordinary play excluded
    assert ctx.cost.total > 0                             # charged real (fake) tokens


def test_stage2_low_confidence_dropped():
    vision = ScriptedVision(lambda n, schema: {"label": "yellow_card", "confidence": 0.2})
    ctx = _ctx(vision)
    Stage2CheapEvents().run(ctx)
    assert ctx.match.events == []


def test_stage2_halts_under_budget():
    vision = ScriptedVision(lambda n, schema: {"label": "yellow_card", "confidence": 0.9})
    ctx = _ctx(vision, cap=0.0)  # no budget at all
    Stage2CheapEvents().run(ctx)
    assert vision.calls == 0                              # never called a model
    assert ctx.match.events == []
    assert any("budget" in w.lower() for w in ctx.match.warnings)


_COACHING = {
    "summary": "You dominated the ball but got countered.",
    "recurring_mistakes": ["High defensive line caught out twice"],
    "positioning_issues": ["Full-backs too advanced on turnovers"],
    "decision_patterns": ["Over-committing the CB to challenges"],
    "practice_drills": ["Practice contain-defending / jockeying"],
}


def test_stage3_produces_one_coaching_report():
    goal = Event(timestamp_ms=1000, category=EventCategory.SCORE_CHANGE, game_event_type="concede")
    vision = ScriptedVision(lambda n, schema: dict(_COACHING))
    ctx = _ctx(vision, events=[goal])
    Stage3CoachingReport().run(ctx)

    assert len(ctx.match.insights) == 1                       # ONE report, not per-goal
    rep = ctx.match.insights[0]
    assert rep.kind == "coaching_report"
    assert rep.summary.startswith("You dominated")
    assert rep.payload["recurring_mistakes"] and rep.payload["practice_drills"]
    assert rep.cost_usd > 0


def test_stage3_halts_under_budget():
    goal = Event(timestamp_ms=1000, category=EventCategory.SCORE_CHANGE, game_event_type="concede")
    vision = ScriptedVision(lambda n, schema: dict(_COACHING))
    ctx = _ctx(vision, cap=0.0, events=[goal])
    Stage3CoachingReport().run(ctx)
    assert vision.calls == 0
    assert ctx.match.insights == []


class CapturingVision:
    free = True

    def __init__(self):
        self.prompts = []

    def generate(self, model, prompt, images_jpeg, schema=None, max_tokens=512):
        self.prompts.append(prompt)
        return VisionResult(data=dict(_COACHING), text="{}", input_tokens=0, output_tokens=0, model=model)


def test_stage3_report_is_grounded_and_personalised():
    goal = Event(timestamp_ms=90000, category=EventCategory.SCORE_CHANGE, game_event_type="concede")
    vis = CapturingVision()
    ctx = _ctx(vis, events=[goal])
    ctx.match.outcome = {"score": "1-2", "result": "loss"}
    ctx.match.capture = {"player_side": "home"}
    Stage3CoachingReport().run(ctx)
    assert vis.prompts, "Stage 3 made no model call"
    p = vis.prompts[0]
    assert "MATCH FACTS" in p and "1-2" in p and "home" in p


def _full_ctx(vision, n_frames=12, **overrides):
    settings = Settings(
        enable_stage_2=False, enable_stage_3=True, stage3_model="claude-sonnet-5",
        coaching_mode="full", coaching_window_frames=3, coaching_max_windows=4, **overrides,
    )
    match = Match(game_id="ea-fc", game_edition="26")
    match.capture = {"player_side": "home"}
    return PipelineContext(
        match=match, adapter=EaFc26Adapter(),
        frames=[_frame(i) for i in range(n_frames)],
        ocr=StubOcrEngine(), settings=settings,
        cost=CostAccountant(cap_usd=1.0), vision=vision, candidate_frames=[],
    )


def _mapreduce_responder(n, schema):
    # Segment (map) calls use the observations schema; the reduce call uses the
    # coaching schema -> return the right shape for each.
    if "observations" in (schema or {}).get("properties", {}):
        return {"observations": [f"segment {n} observation"]}
    return dict(_COACHING)


def test_stage3_full_reads_every_segment_then_synthesises():
    vision = ScriptedVision(_mapreduce_responder)
    ctx = _full_ctx(vision)  # 12 frames / window 3 -> 4 segment reads + 1 synthesis
    Stage3CoachingReport().run(ctx)

    assert vision.calls == 5
    assert len(ctx.match.insights) == 1
    rep = ctx.match.insights[0]
    assert rep.kind == "coaching_report"
    assert rep.payload["segments_read"] == 4
    assert rep.payload["frames_reviewed"] == 12
    assert rep.summary.startswith("You dominated")
    assert rep.cost_usd > 0


def test_stage3_full_corrects_score_from_vision_reads():
    # The real bug: OCR read the match as 4-8 when it was 4-1. In full mode the
    # model READS the scoreboard; end-of-match consensus corrects the OCR.
    def responder(n, schema):
        if "observations" in (schema or {}).get("properties", {}):
            return {"observations": [f"seg {n}"], "score_home": 4, "score_away": 1}
        return dict(_COACHING)

    vision = ScriptedVision(responder)
    ctx = _full_ctx(vision)
    ctx.match.outcome = {"score": "4-8", "result": "loss", "score_home": 4, "score_away": 8}
    Stage3CoachingReport().run(ctx)

    assert ctx.match.outcome["score_home"] == 4
    assert ctx.match.outcome["score_away"] == 1        # corrected from 8
    assert ctx.match.outcome["result"] == "win"        # 4-1 for the home player
    assert ctx.match.outcome["score_source"] == "vision_corrected"
    assert ctx.match.outcome["score_ocr"] == "4-8"
    assert any("corrected" in w.lower() for w in ctx.match.warnings)


def test_stage3_full_keeps_ocr_score_when_vision_agrees():
    def responder(n, schema):
        if "observations" in (schema or {}).get("properties", {}):
            return {"observations": [f"seg {n}"], "score_home": 2, "score_away": 3}
        return dict(_COACHING)

    vision = ScriptedVision(responder)
    ctx = _full_ctx(vision)
    ctx.match.outcome = {"score": "2-3", "result": "loss", "score_home": 2, "score_away": 3}
    Stage3CoachingReport().run(ctx)
    assert "score_source" not in ctx.match.outcome           # no override when they agree
    assert not any("corrected" in w.lower() for w in ctx.match.warnings)


def test_stage3_full_stops_reading_when_budget_runs_out():
    # Tiny cap: it reads a segment or two, runs out, and still produces a report
    # from whatever it managed to read (never crashes, never overspends).
    vision = ScriptedVision(_mapreduce_responder, in_tok=5000, out_tok=400)
    ctx = _full_ctx(vision)
    ctx.cost = CostAccountant(cap_usd=0.02)
    Stage3CoachingReport().run(ctx)
    assert ctx.cost.total <= 0.02
    assert any("budget" in w.lower() for w in ctx.match.warnings)


def test_gemini_video_stage_builds_report_and_score(monkeypatch):
    # Native whole-video path: mock the Gemini call, verify it yields ONE coaching
    # report (with strengths) and sets the score from what the model read.
    import core.ai.gemini_video as gv
    from types import SimpleNamespace

    class FakeVid:
        def __init__(self, *a, **k):
            pass

        def prepare(self, *a, **k):
            return "files/fake"

        def analyze_uri(self, *a, **k):  # watch pass -> timestamped observation log + score
            return SimpleNamespace(
                data={"observations": [
                    {"time": "00:10", "note": "high press won the ball in the final third"},
                    {"time": "01:20", "note": "fullbacks caught upfield on the counter attack"}],
                      "score": {"home": 4, "away": 1}},
                text="{}", input_tokens=5000, output_tokens=300,
                model="gemini-flash-latest", cost_usd=0.01,
            )

        def generate_text(self, **k):  # pass 2: deep synthesis
            return SimpleNamespace(
                data={"summary": "Controlled 4-1 win.", "strengths": ["good pressing"],
                      "recurring_mistakes": ["late tracking back"], "positioning_issues": [],
                      "decision_patterns": [], "practice_drills": ["1v1 defending"]},
                text="{}", input_tokens=800, output_tokens=200,
                model="gemini-pro-latest", cost_usd=0.02,
            )

        def research(self, **k):  # self-learning
            return {"answer": "The Box Crasher makes late runs into the box.",
                    "sources": ["https://example.com/fc26"], "cost_usd": 0.0}

    monkeypatch.setattr(gv, "GeminiVideoModel", FakeVid)
    from core.pipeline.stages import GeminiVideoCoaching

    settings = Settings(openai_api_key="k", gemini_video_model="gemini-flash-latest",
                        gemini_video_watch_passes=2,  # exercise self-consistency
                        gemini_video_compress=False, enable_roster_ocr=False)  # no ffmpeg in tests
    match = Match(game_id="ea-fc", game_edition="26")
    match.source_type = SourceType.VIDEO_NATIVE
    match.capture = {"player_side": "home"}
    ctx = PipelineContext(
        match=match, adapter=EaFc26Adapter(), frames=[], ocr=StubOcrEngine(),
        settings=settings, cost=CostAccountant(cap_usd=0.40), source_bytes=b"fakevideo",
    )
    assert GeminiVideoCoaching().enabled(ctx)
    GeminiVideoCoaching().run(ctx)

    assert match.outcome["score_home"] == 4 and match.outcome["score_away"] == 1
    assert match.outcome["result"] == "win"
    assert match.outcome["score_source"] == "gemini_video"
    assert len(match.insights) == 1
    rep = match.insights[0]
    assert rep.kind == "coaching_report"
    assert rep.summary.startswith("Controlled")       # from the synthesis pass
    assert rep.payload["strengths"] == ["good pressing"]
    assert rep.payload["observations_count"] == 2            # 2 viewings merged -> 2 obs
    assert rep.payload["corroborated_count"] == 2            # both seen in both viewings
    assert round(ctx.cost.total, 3) == 0.04                 # 2 watch x 0.01 + synth 0.02


def test_self_learning_queues_and_researches_gaps(monkeypatch):
    # The observe pass flags a gap; the stage queues it and researches it via the
    # (mocked) grounded model, filing a learned fact. KB writes are mocked so the
    # test never touches the repo's knowledge files.
    import core.ai.gemini_video as gv
    from types import SimpleNamespace
    from adapters.ea_fc_26 import knowledge_base as kb

    calls = {"queued": None, "resolved": []}
    monkeypatch.setattr(kb, "queue_gaps", lambda qs, match_id="": calls.__setitem__("queued", list(qs)))
    monkeypatch.setattr(kb, "open_gaps",
                        lambda limit=None: [{"id": "box-crasher", "question": "what is the Box Crasher role?"}])
    monkeypatch.setattr(kb, "resolve_gap",
                        lambda gid, ans, srcs, tags=None: calls["resolved"].append((gid, ans)))

    class FakeVid:
        def __init__(self, *a, **k):
            pass

        def prepare(self, *a, **k):
            return "files/fake"

        def analyze_uri(self, *a, **k):
            return SimpleNamespace(
                data={"observations": [{"time": "01:00", "note": "used an unfamiliar role in midfield"}],
                      "score": {"home": 2, "away": 0},
                      "knowledge_gaps": ["what is the Box Crasher role?"]},
                text="{}", input_tokens=5000, output_tokens=300,
                model="gemini-flash-latest", cost_usd=0.01)

        def generate_text(self, **k):
            return SimpleNamespace(
                data={"summary": "Solid win.", "strengths": ["press"], "recurring_mistakes": [],
                      "positioning_issues": [], "decision_patterns": [], "practice_drills": []},
                text="{}", input_tokens=800, output_tokens=100,
                model="gemini-pro-latest", cost_usd=0.005)

        def research(self, **k):
            return {"answer": "The Box Crasher makes late trailing runs into the opponent's box.",
                    "sources": ["https://ea.com/fc26"], "cost_usd": 0.001}

    monkeypatch.setattr(gv, "GeminiVideoModel", FakeVid)
    from core.pipeline.stages import GeminiVideoCoaching

    settings = Settings(openai_api_key="k", enable_self_learning=True, learn_max_gaps_per_run=3,
                        gemini_video_compress=False, enable_roster_ocr=False)
    match = Match(game_id="ea-fc", game_edition="26")
    match.source_type = SourceType.VIDEO_NATIVE
    match.capture = {"player_side": "home"}
    ctx = PipelineContext(
        match=match, adapter=EaFc26Adapter(), frames=[], ocr=StubOcrEngine(),
        settings=settings, cost=CostAccountant(cap_usd=1.0), source_bytes=b"vid",
    )
    GeminiVideoCoaching().run(ctx)

    assert calls["queued"] == ["what is the Box Crasher role?"]
    assert calls["resolved"] and calls["resolved"][0][0] == "box-crasher"
    assert "Box Crasher" in calls["resolved"][0][1]


def test_roster_reader_aggregates_names_per_side(monkeypatch):
    # OCR the fixed badge regions across frames -> real squad per side. LEFT badge
    # = home, RIGHT = away. Names seen >= min_count are kept.
    import core.extraction.rosters as rz

    monkeypatch.setattr(rz, "_extract_frames",
                        lambda vb, e, m: [np.zeros((100, 200, 3), np.uint8) for _ in range(3)])

    class FakeOcr:
        def __init__(self):
            self.n = 0

        def read_text(self, img, whitelist=None):
            self.n += 1
            return ("6 MAINOO", 0.9) if self.n % 2 == 1 else ("GORETZKA 8", 0.9)

    regions = {"home": (0.0, 0.0, 0.2, 0.1), "away": (0.8, 0.0, 0.2, 0.1)}
    out = rz.read_rosters(b"vid", FakeOcr(), regions, every_s=5, max_frames=3, min_count=2)
    assert out["home"] == ["MAINOO"]      # left badge, digits stripped
    assert out["away"] == ["GORETZKA"]    # right badge


def test_highlights_disabled_without_object_store():
    # _ctx sets no object_store -> the clip stage must not run (needs storage).
    ctx = _ctx(ScriptedVision(lambda n, s: {}))
    assert HighlightClips().enabled(ctx) is False
