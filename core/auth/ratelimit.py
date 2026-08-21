"""Per-key rate limiting over Redis - fixed window, two commands per check.

Built for the auth endpoints, where the scarce resource is not our CPU but the
Supabase email quota: the built-in sender allows only a handful of sign-in
emails PER HOUR FOR THE WHOLE PROJECT, so one scripted visitor hammering
/magic-link locks every real user out of sign-in for the hour. A fixed window
(INCR + EXPIRE on first hit) is enough for that - the burst-at-window-edge
imprecision of fixed windows does not matter at these limits.

FAILS OPEN on Redis errors, deliberately: sign-in continuing to work through a
Redis blip beats sign-in going down with it. Redis is already in the stack for
Celery, so this adds no new moving part.
"""
from __future__ import annotations

import logging

import redis as redis_lib

log = logging.getLogger("coachio.ratelimit")


class RateLimiter:
    def __init__(self, client) -> None:
        self._r = client

    def allow(self, key: str, limit: int, window_s: int) -> bool:
        """One hit against `key`. True while the window has room."""
        try:
            count = self._r.incr(key)
            if count == 1:
                self._r.expire(key, window_s)
            return count <= limit
        except redis_lib.RedisError as exc:
            log.warning("rate limit check failed for %s (allowing): %s", key, exc)
            return True


_limiter: RateLimiter | None = None


def get_rate_limiter(settings) -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(redis_lib.Redis.from_url(settings.redis_url))
    return _limiter
