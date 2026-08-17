"""Regression tests for FC 26 score aggregation.

These encode the real failure modes we hit on actual footage:
- a 6-frame corner-kick phantom read as a confident "8" (final was 1-1)
- a two-digit crest misread ("94") that must never become a score
- the difference between a transient phantom and a genuine goal
so the trailing-window aggregation can't silently regress.
"""
from adapters.base.hud_schema import HudRegion
from adapters.base.interface import RegionReading
from adapters.ea_fc_26.adapter import EaFc26Adapter
from core.models.domain import Match, player_scoreline
from core.pipeline.stages import GOAL_READ_LEAD_S, _mmss, _restate_result

adapter = EaFc26Adapter()


class TestGoalTimestampLead:
    """The scoreboard is a LAGGING indicator - it only changes once the ball is
    already in, often after a cut to the celebration. So a scoreboard-derived goal
    time is always late, and is backed off at the source to land on the build-up.
    """

    def test_lead_is_ten_seconds(self):
        assert GOAL_READ_LEAD_S == 10

    def test_lead_is_applied_and_clamped_at_zero(self):
        # The expression used in _read_score_timeline.
        def shifted(read_at: int) -> int:
            return max(0, int(read_at) - GOAL_READ_LEAD_S)

        assert shifted(75) == 65
        assert shifted(10) == 0
        assert shifted(4) == 0      # would be -6 without the clamp
        assert shifted(0) == 0

    def test_shifted_time_still_formats_as_mmss(self):
        assert _mmss(max(0, 75 - GOAL_READ_LEAD_S)) == "01:05"

_CLOCK = HudRegion(name="clock", rect=(0.0, 0.0, 0.05, 0.05), meta={"role": "clock"})
_HOME = HudRegion(name="score_home", rect=(0.1, 0.0, 0.05, 0.05), meta={"role": "score", "team": "home"})
_AWAY = HudRegion(name="score_away", rect=(0.1, 0.1, 0.05, 0.05), meta={"role": "score", "team": "away"})


def _reads(frames, conf=0.99):
    """frames: list of (clock_text, home_text, away_text) -> flat RegionReading list."""
    out = []
    for i, (clk, home, away) in enumerate(frames):
        ts = i * 1000
        out.append(RegionReading(region=_CLOCK, frame_index=i, timestamp_ms=ts, text=clk, confidence=conf))
        out.append(RegionReading(region=_HOME, frame_index=i, timestamp_ms=ts, text=home, confidence=conf))
        out.append(RegionReading(region=_AWAY, frame_index=i, timestamp_ms=ts, text=away, confidence=conf))
    return out


def test_transient_phantom_is_rejected():
    """The exact bug: match is 1-1, a 6-frame set-piece phantom reads 94/8 at
    full confidence. Final must stay 1-1."""
    frames = [("77:00", "1", "1")] * 30
    for i in range(10, 16):  # 6 consecutive phantom frames
        frames[i] = ("77:03", "94", "8")
    parsed = adapter.interpret(_reads(frames))
    assert parsed.outcome["score_home"] == 1
    assert parsed.outcome["score_away"] == 1
    # No bogus concede-to-8 event.
    assert all(e.payload.get("score", 0) <= 1 for e in parsed.events)


def test_implausible_two_digit_rejected():
    """A crest misread as '94' is above any real FC score and must be dropped;
    it must not leave the score stuck or inflated."""
    frames = [("10:00", "94", "3")] * 20
    parsed = adapter.interpret(_reads(frames))
    assert parsed.outcome["score_home"] == 0  # 94 never accepted -> no home reads
    assert parsed.outcome["score_away"] == 3


def test_real_goal_is_tracked():
    """A genuine goal (0->1 sustained to the end) yields final 1 and an event."""
    frames = [("40:00", "0", "0")] * 20 + [("70:00", "1", "0")] * 20
    parsed = adapter.interpret(_reads(frames))
    assert parsed.outcome["score_home"] == 1
    assert parsed.outcome["score_away"] == 0
    goals = [e for e in parsed.events if e.game_event_type == "goal" and e.payload["score"] == 1]
    assert len(goals) == 1


def test_frames_without_valid_clock_are_ignored():
    """Cutscene frames (no parseable clock) must not contribute score reads,
    even when they land at the end of the clip."""
    live = [("80:00", "2", "1")] * 20
    cutscene = [("REPLAY", "9", "9")] * 10  # invalid clock -> gated out
    parsed = adapter.interpret(_reads(live + cutscene))
    assert parsed.outcome["score_home"] == 2
    assert parsed.outcome["score_away"] == 1


def test_prekickoff_intro_scoreboard_is_ignored():
    """The real bug from match 445fd3ab: a pre-kickoff intro/recap overlay sat at
    clock 00:00 while the score graphic animated up to 8, producing seven phantom
    'concede' events before kickoff. Score reads at 00:00 (clock not running) must
    be ignored; only live play (clock > 0:00) counts. Real match here is 1-0."""
    intro = [("00:00", "4", str(min(i, 8))) for i in range(1, 9)]   # overlay animates away 1->8
    live = [("05:00", "1", "0")] * 20                                # real match: 1-0
    parsed = adapter.interpret(_reads(intro + live))
    assert parsed.outcome["score_home"] == 1
    assert parsed.outcome["score_away"] == 0
    # No pre-kickoff phantom events, and nothing above the real score.
    assert all(e.timestamp_ms >= 8000 for e in parsed.events)
    assert all(e.payload.get("score", 0) <= 1 for e in parsed.events)


def test_final_reflects_end_not_max():
    """Score can only be read as the end-of-match value. A high early phantom
    that reverts must lose to the sustained ending."""
    frames = [("10:00", "0", "5")] * 8 + [("85:00", "0", "2")] * 25  # early '5' phantom, true 2
    parsed = adapter.interpret(_reads(frames))
    assert parsed.outcome["score_away"] == 2


class TestScorelineOrdering:
    """One match must not show two different scorelines on one screen.

    A real report rendered the title from outcome["score"] (home-away, straight
    off the HUD) while the body was restated player-first, so an away player read
    "4-3" in the header and "3-4 Loss" one line below it. Both now come from
    player_scoreline.
    """

    def test_away_player_sees_their_goals_first(self):
        assert player_scoreline({"score_home": 4, "score_away": 3}, "away") == "3-4"

    def test_home_player_is_unchanged(self):
        assert player_scoreline({"score_home": 4, "score_away": 3}, "home") == "4-3"

    def test_missing_split_score_returns_none_so_callers_can_fall_back(self):
        assert player_scoreline({"score": "4-3"}, "away") is None

    def test_match_scoreline_falls_back_to_the_raw_string(self):
        m = Match(game_id="ea-fc", game_edition="26", outcome={"score": "4-3"})
        assert m.scoreline() == "4-3"

    def test_title_and_body_agree_for_an_away_player(self):
        outcome = {"score": "4-3", "score_home": 4, "score_away": 3}
        match = Match(game_id="ea-fc", game_edition="26", outcome=outcome,
                      capture={"player_side": "away"})
        report: dict = {"match_context": {"result": "whatever the model said"}}
        _restate_result(report, outcome, "away")
        # The header renders match.scoreline(); the body renders this line.
        assert report["match_context"]["result"].startswith(match.scoreline())
        assert report["match_context"]["result"] == "3-4 Loss"

    def test_verdict_follows_the_player_not_the_home_side(self):
        outcome = {"score_home": 1, "score_away": 3}
        report: dict = {"match_context": {}}
        _restate_result(report, outcome, "away")
        assert report["match_context"]["result"] == "3-1 Win"
