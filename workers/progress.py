"""Progress bus over Redis pub/sub. The worker publishes; the API's SSE endpoint
subscribes and streams to the browser."""
from __future__ import annotations

import json

import redis

from core.config import get_settings
from core.pipeline.context import ProgressReporter


from core.progress.bus import channel_for, heartbeat_key  # noqa: E402,F401 - re-export; tools import these from here


class RedisProgressReporter(ProgressReporter):
    def __init__(self, match_id: str) -> None:
        self._match_id = match_id
        self._r = redis.Redis.from_url(get_settings().redis_url)
        self._channel = channel_for(match_id)
        self._hb_ttl = get_settings().match_heartbeat_ttl_s

    def report(self, stage: str, status: str, detail: str = "", **extra) -> None:
        payload = {"stage": stage, "status": status, "detail": detail, **extra}
        self._r.publish(self._channel, json.dumps(payload, default=str))
        # Liveness stamp for the stuck-match watchdog (core/progress/watchdog.py):
        # pub/sub is fire-and-forget, so without this a dead worker is
        # indistinguishable from a quiet one. The key expiring IS the signal.
        self._r.setex(heartbeat_key(self._match_id), self._hb_ttl, payload["status"])
