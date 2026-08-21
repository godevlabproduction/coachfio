"""Stuck-match watchdog + the signed-in gate on match data.

Pure logic, no DB/Redis - the decision functions are extracted precisely so
the rules that decide "this run is dead" and "this visitor may not see match
data" can be pinned here; the plumbing is exercised against the live stack.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from core.models.enums import MatchStatus
from core.progress.watchdog import is_stale

NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
GRACE = 300


def _age(seconds: int) -> datetime:
    return NOW - timedelta(seconds=seconds)


class TestStaleDecision:
    def test_processing_with_dead_heartbeat_and_old_enough_is_stale(self):
        assert is_stale(MatchStatus.PROCESSING.value, _age(GRACE + 1),
                        heartbeat_alive=False, grace_s=GRACE, now=NOW)

    def test_live_heartbeat_is_never_stale(self):
        """However old the row: a worker that is reporting is a worker that is
        working - deep mode legitimately runs for a long time."""
        assert not is_stale(MatchStatus.PROCESSING.value, _age(10_000),
                            heartbeat_alive=True, grace_s=GRACE, now=NOW)

    def test_young_rows_get_the_grace_period(self):
        """The gap between enqueue and the worker's first report must not read
        as a death."""
        assert not is_stale(MatchStatus.PROCESSING.value, _age(GRACE - 5),
                            heartbeat_alive=False, grace_s=GRACE, now=NOW)

    @pytest.mark.parametrize("status", ["complete", "failed", "queued", "created"])
    def test_only_processing_can_be_stale(self, status):
        assert not is_stale(status, _age(10_000),
                            heartbeat_alive=False, grace_s=GRACE, now=NOW)

    def test_naive_timestamps_do_not_crash(self):
        """Rows can come back tz-naive depending on the driver; the sweep must
        not 500 the match page over it."""
        naive = (NOW - timedelta(seconds=GRACE + 1)).replace(tzinfo=None)
        assert is_stale(MatchStatus.PROCESSING.value, naive,
                        heartbeat_alive=False, grace_s=GRACE, now=NOW)

    def test_missing_timestamp_is_not_stale(self):
        assert not is_stale(MatchStatus.PROCESSING.value, None,
                            heartbeat_alive=False, grace_s=GRACE, now=NOW)


class TestRequireUser:
    """Match data is per-account; the shared "anonymous" identity must stop
    counting as an account the moment real sign-in exists."""

    def _configure(self, monkeypatch, supabase_on: bool):
        from api import deps
        monkeypatch.setattr(deps._settings, "supabase_url",
                            "https://example.supabase.co" if supabase_on else "")
        monkeypatch.setattr(deps._settings, "supabase_anon_key",
                            "anon" if supabase_on else "")
        monkeypatch.setattr(deps, "read_session",
                            lambda request, settings: None)  # nobody signed in

    def test_anonymous_is_refused_once_supabase_is_on(self, monkeypatch):
        from api.deps import require_user
        self._configure(monkeypatch, supabase_on=True)
        with pytest.raises(HTTPException) as exc:
            require_user(request=None, x_user_id=None)
        assert exc.value.status_code == 401

    def test_anonymous_still_works_in_keyless_dev(self, monkeypatch):
        """Without a provider, anonymous is the only identity there is -
        refusing it would brick local development."""
        from api.deps import require_user
        self._configure(monkeypatch, supabase_on=False)
        assert require_user(request=None, x_user_id=None) == "anonymous"

    def test_a_real_session_passes_regardless(self, monkeypatch):
        from api import deps
        self._configure(monkeypatch, supabase_on=True)
        monkeypatch.setattr(deps, "read_session", lambda request, settings: "user-123")
        assert deps.require_user(request=None, x_user_id=None) == "user-123"
