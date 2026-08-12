"""Progress bus over Redis pub/sub. The worker publishes; the API's SSE endpoint
subscribes and streams to the browser."""
from __future__ import annotations

import json

import redis

from core.config import get_settings
from core.pipeline.context import ProgressReporter


def channel_for(match_id: str) -> str:
    return f"match-progress:{match_id}"


class RedisProgressReporter(ProgressReporter):
    def __init__(self, match_id: str) -> None:
        self._match_id = match_id
        self._r = redis.Redis.from_url(get_settings().redis_url)
        self._channel = channel_for(match_id)

    def report(self, stage: str, status: str, detail: str = "", **extra) -> None:
        payload = {"stage": stage, "status": status, "detail": detail, **extra}
        self._r.publish(self._channel, json.dumps(payload, default=str))
