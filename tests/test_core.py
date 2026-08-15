"""Pure-logic tests - no OCR, ffmpeg, DB, or network. Run in the backend image:
    docker compose run --rm api pytest
"""
from __future__ import annotations

from pathlib import Path

import pytest

from adapters.base.interface import RegionReading
from adapters.ea_fc_26.adapter import EaFc26Adapter
from core.models.domain import Match, Metric
from core.pipeline.cost import BudgetExceeded, CostAccountant
from core.progress.trends import build_trends


def _reading(adapter, region_name, text, conf, ts, stat):
    region = adapter.hud_schema().region(region_name)
    assert region is not None, region_name
    return RegionReading(
        region=region, frame_index=ts // 1000, timestamp_ms=ts, text=text,
        confidence=conf, is_stat_screen=stat,
    )


def test_fc26_interpret_score_and_stats():
    adapter = EaFc26Adapter()
    # Realistic 1fps sampling: each score persists across several frames, the way
    # a real HUD does. A score must be corroborated (not a single frame) before it
    # counts - that's what makes the pipeline robust to transient OCR phantoms.
    readings = []

    def phase(home, away, start_frame, n=4):
        for k in range(n):
            f = start_frame + k
            ts = f * 1000
            clk = f"{f // 60:02d}:{f % 60:02d}"
            readings.append(_reading(adapter, "clock", clk, 0.9, ts, False))
            readings.append(_reading(adapter, "score_home", home, 0.9, ts, False))
            readings.append(_reading(adapter, "score_away", away, 0.9, ts, False))

    phase("0", "0", 0)     # kickoff
    phase("1", "0", 30)    # home goal
    phase("2", "0", 60)    # home goal
    phase("2", "1", 75, n=8)  # concede, and this is the sustained ending -> final 2-1

    readings += [
        _reading(adapter, "ss_title", "FULL TIME", 0.95, 200000, True),
        _reading(adapter, "ss_possession_home", "57%", 0.9, 200000, True),
        _reading(adapter, "ss_shots_home", "12", 0.9, 200000, True),
    ]
    parsed = adapter.interpret(readings)

    assert parsed.outcome["score"] == "2-1"
    assert parsed.outcome["result"] == "win"
    assert parsed.outcome["stat_screen_found"] is True

    keyed = {m.key: m.value for m in parsed.metrics}
    assert keyed["score_home"] == 2.0
    assert keyed["score_away"] == 1.0
    assert keyed["possession_home_pct"] == 57.0
    assert keyed["shots_home"] == 12.0

    goals = [e for e in parsed.events if e.game_event_type == "goal"]
    assert len(goals) == 2  # two home increments (to 1, then to 2)


def test_fc26_final_score_is_end_state_not_max():
    adapter = EaFc26Adapter()
    # A high early phantom (3) that reverts must lose to the sustained ending (1).
    readings = []
    for f in range(4):  # brief phantom at the start
        readings.append(_reading(adapter, "clock", "01:00", 0.9, f * 1000, False))
        readings.append(_reading(adapter, "score_home", "3", 0.9, f * 1000, False))
    for f in range(10, 30):  # sustained real value to the end
        readings.append(_reading(adapter, "clock", "45:00", 0.9, f * 1000, False))
        readings.append(_reading(adapter, "score_home", "1", 0.9, f * 1000, False))
    parsed = adapter.interpret(readings)
    assert parsed.outcome["score_home"] == 1


def test_budget_enforced():
    acc = CostAccountant.for_match(0.25, duration_ms=15 * 60 * 1000)
    assert acc.cap_usd == pytest.approx(0.25)
    acc.charge("stage2", 0.20)
    with pytest.raises(BudgetExceeded):
        acc.charge("stage3", 0.10)  # would total 0.30 > 0.25
    assert acc.total == pytest.approx(0.20)


def test_budget_scales_with_length():
    acc = CostAccountant.for_match(0.25, duration_ms=30 * 60 * 1000)
    assert acc.cap_usd == pytest.approx(0.50)  # 30 min => 2x cap


def test_trends_improving_direction():
    def mk(score_home):
        m = Match(game_id="ea-fc", game_edition="26")
        m.metrics = [Metric(key="score_home", label="Goals", value=score_home, higher_is_better=True)]
        return m

    m1, m2 = mk(1.0), mk(3.0)
    m2.created_at = m2.created_at.replace(microsecond=m1.created_at.microsecond + 1)
    trends = build_trends([m1, m2])
    t = next(t for t in trends if t.key == "score_home")
    assert t.delta == 2.0
    assert t.improving is True


def test_no_game_branching_in_core():
    """The design rule, enforced: /core must not branch on a specific game.

    We scan executable lines (comments stripped) for game-id string literals and
    for `game[_id] == ...` comparisons - the concrete 'if game == "fc26"' smell
    the brief warns about. Prose mentioning football in docstrings is fine; a
    hardcoded game id in code is not.
    """
    import re

    core_dir = Path(__file__).resolve().parent.parent / "core"
    smells = [
        re.compile(r"""["'](ea-fc|fc-?26|nba-?2k|rocket-?league)["']""", re.I),
        re.compile(r"""game(_id)?\s*==\s*["']"""),
        re.compile(r"""if\s+game\b"""),
    ]
    offenders = []
    for py in core_dir.rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]  # drop trailing/whole-line comments
            if any(rx.search(code) for rx in smells):
                offenders.append(f"{py.relative_to(core_dir)}:{i}: {line.strip()}")
    assert not offenders, f"game-specific branching leaked into /core: {offenders}"
