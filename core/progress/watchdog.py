"""Stuck-match watchdog.

A match stays "processing" forever if its worker dies mid-run (kill -9, OOM,
redeploy) - the frozen progress bar the user is staring at will never move
again, and nothing in the system knows. The worker stamps a Redis heartbeat
with a TTL on every progress report; this module is the other half: when
anyone LOOKS at a processing match whose heartbeat has expired, it is marked
failed with a message worth reading.

Swept on read rather than by a scheduler on purpose: it needs no extra
process (no Celery beat to deploy and forget), and "when someone looks" is
exactly when a wrong status does damage.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import redis as redis_lib

from core.models.enums import MatchStatus

log = logging.getLogger("coachio.watchdog")

_DETAIL = ("the analysis stopped unexpectedly (the worker went away mid-run) - "
           "nothing is wrong with your video. Run the analysis again.")


def is_stale(status: str, updated_at: datetime | None, heartbeat_alive: bool,
             grace_s: int, now: datetime | None = None) -> bool:
    """The decision alone, dependency-free: a processing match with no live
    heartbeat, old enough that 'the worker has not reported YET' is ruled out."""
    if status != MatchStatus.PROCESSING.value or heartbeat_alive or updated_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    if updated_at.tzinfo is None:  # SQLite/test rows may come back naive
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return (now - updated_at) > timedelta(seconds=grace_s)


def fail_if_stale(session, match_id: str, settings) -> bool:
    """Mark one stuck match failed. True if it was stuck and is now failed.

    Called from the read paths (match GET, progress snapshot, list). Cheap for
    the common case: one Redis EXISTS for a processing match, nothing at all
    for terminal ones - callers check status before calling.
    """
    from core.progress.bus import channel_for, heartbeat_key
    from core.storage.models import MatchRow

    row = session.get(MatchRow, match_id)
    if row is None or row.status != MatchStatus.PROCESSING.value:
        return False
    try:
        r = redis_lib.Redis.from_url(settings.redis_url)
        alive = bool(r.exists(heartbeat_key(match_id)))
    except redis_lib.RedisError as exc:
        # Redis being down must not turn every processing match into a failure.
        log.warning("watchdog could not check heartbeat for %s: %s", match_id, exc)
        return False
    if not is_stale(row.status, row.updated_at, alive, settings.match_stale_grace_s):
        return False

    row.status = MatchStatus.FAILED.value
    row.warnings = [*(row.warnings or []), f"watchdog: {_DETAIL}"]
    session.flush()
    log.warning("watchdog failed stuck match %s (no heartbeat)", match_id)
    try:
        # Close any open progress stream too - the SSE loop exits on a terminal
        # event, and this is that event for a run whose worker cannot send one.
        # Published directly (not via the worker's reporter, which would stamp a
        # fresh heartbeat for a run that is being declared dead).
        import json
        r.publish(channel_for(match_id), json.dumps({
            "stage": "pipeline", "status": "failed", "detail": _DETAIL,
            "match_status": MatchStatus.FAILED.value}))
    except redis_lib.RedisError:
        pass  # the DB status is already right; the stream will see it on refresh
    return True
