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


def test_core_carries_no_game_specific_prompt_language():
    """The companion to test_no_game_branching_in_core, one level down.

    A game id never leaked into /core, but its VOCABULARY did: the prompts said
    "an elite EA Sports FC 26 coach", "home is the TOP row", "jockey with L2/LT",
    "switch to your left winger". That is as game-specific as `if game == "fc26"`,
    it just does not look like it. The words now come from
    `GameAdapter.prompt_fragments()`; the core owns only the SHAPE of a prompt.

    Comments are exempt: several explain a defence by citing the real bug that
    motivated it, and rewriting those to be game-neutral would delete the reason.
    """
    import re
    from pathlib import Path

    banned = re.compile(
        r"\b(FC ?2[0-9]|EA Sports|winger|striker|jockey|fullback|full-back|goalkeeper"
        r"|midfielder|centre-back|half-space|cutback|six-yard)\b", re.I)

    offenders = []
    for path in Path("core").rglob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if banned.search(code):
                offenders.append(f"{path}:{n}: {line.strip()[:90]}")
    assert not offenders, "game vocabulary in /core:\n" + "\n".join(offenders)


# --- session transport -------------------------------------------------------
# These cover the CARRIER, not authentication. Proving someone owns an email is
# the hosted provider's job; nothing here checks a credential.

def _settings():
    from core.config import Settings
    return Settings(secret_key="test-key-not-the-default")


def test_a_session_token_round_trips():
    from core.auth.session import make_token, read_token

    s = _settings()
    assert read_token(make_token("user-123", s), s) == "user-123"


def test_a_tampered_session_is_rejected_not_trusted():
    """The signature covers the value, so swapping in another account's id
    invalidates it. Without this the cookie would be a self-service login."""
    from core.auth.session import make_token, read_token

    s = _settings()
    good = make_token("user-123", s)
    forged = "user-999" + good[good.index("."):]
    assert read_token(forged, s) is None
    assert read_token(good + "x", s) is None
    assert read_token("", s) is None
    assert read_token("nonsense", s) is None


def test_a_token_signed_with_another_key_is_rejected():
    from core.config import Settings
    from core.auth.session import make_token, read_token

    other = Settings(secret_key="a-different-deployment")
    assert read_token(make_token("user-123", other), _settings()) is None


def test_a_session_cannot_be_replayed_as_a_handoff_or_the_reverse():
    """Different salts. A 30-day session presented as a one-minute hand-off would
    defeat the point of the hand-off being short-lived."""
    from core.auth.session import make_handoff, make_token, read_handoff, read_token

    s = _settings()
    assert read_handoff(make_token("user-123", s), s) is None
    assert read_token(make_handoff("user-123", s), s) is None


def test_an_expired_session_is_rejected():
    from core.config import Settings
    from core.auth.session import make_token, read_token

    expired = Settings(secret_key="test-key-not-the-default", session_max_age_days=0)
    import time
    tok = make_token("user-123", expired)
    time.sleep(1.1)
    assert read_token(tok, expired) is None


# --- the seam trusts the cookie and nothing else ------------------------------
# Both of these were live until Supabase worked: an unverified header and an
# unverified query parameter, either of which let a caller be any account by
# knowing its id. They are the reason "connect a provider" was only half the job.

def _req(headers=None, cookies=None):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        raw.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    return Request({"type": "http", "headers": raw, "query_string": b"", "method": "GET",
                    "path": "/", "scheme": "http", "server": ("test", 80)})


def test_an_unverified_header_is_not_an_identity():
    from api.deps import current_user

    got = current_user(_req(), x_user_id="somebody-elses-id")
    assert got == "anonymous", "the X-User-Id header still grants an account"


def test_a_valid_session_cookie_is_an_identity():
    from api.deps import _settings, current_user
    from core.auth.session import SESSION_COOKIE, make_token

    tok = make_token("user-123", _settings)
    assert current_user(_req(cookies={SESSION_COOKIE: tok})) == "user-123"


def test_a_forged_cookie_is_not():
    from api.deps import current_user
    from core.auth.session import SESSION_COOKIE

    assert current_user(_req(cookies={SESSION_COOKIE: "user-123.forged.sig"})) == "anonymous"


def test_the_dev_header_works_only_when_explicitly_enabled(monkeypatch):
    """The CLI needs it; production must not have it. Enabling it is a deliberate
    act, and api/main.py refuses to boot with it on behind HTTPS."""
    import api.deps as deps

    monkeypatch.setattr(deps._settings, "allow_dev_user_header", True)
    assert deps.current_user(_req(), x_user_id="cli-user") == "cli-user"

    monkeypatch.setattr(deps._settings, "allow_dev_user_header", False)
    assert deps.current_user(_req(), x_user_id="cli-user") == "anonymous"


def test_current_user_no_longer_accepts_a_query_parameter():
    """`?u=` put an account id in URLs, so it landed in access logs and Referer
    headers. The cookie reaches the SSE stream, <video> and PDF links instead."""
    import inspect

    from api.deps import current_user

    assert "u" not in inspect.signature(current_user).parameters
