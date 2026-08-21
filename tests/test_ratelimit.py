"""Rate limiter: the auth endpoints' guard on the shared sign-in email quota.

Fake Redis client, pure logic - the wiring (which endpoints, what limits) is
declared on the routes in api/routes/auth.py and exercised live.
"""
from __future__ import annotations

import redis as redis_lib

from core.auth.ratelimit import RateLimiter


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key: str, seconds: int) -> None:
        self.ttls[key] = seconds


class BrokenRedis:
    def incr(self, key: str):
        raise redis_lib.ConnectionError("redis is down")


class TestRateLimiter:
    def test_allows_up_to_the_limit_then_refuses(self):
        rl = RateLimiter(FakeRedis())
        results = [rl.allow("rl:test:1.2.3.4", 3, 3600) for _ in range(5)]
        assert results == [True, True, True, False, False]

    def test_keys_are_independent(self):
        """One abusive IP must not consume anyone else's budget."""
        rl = RateLimiter(FakeRedis())
        for _ in range(10):
            rl.allow("rl:test:attacker", 3, 3600)
        assert rl.allow("rl:test:bystander", 3, 3600)

    def test_window_expiry_is_set_once_on_first_hit(self):
        """EXPIRE only on the first INCR: re-arming it every hit would turn a
        fixed window into 'banned for as long as you keep trying'."""
        fake = FakeRedis()
        rl = RateLimiter(fake)
        rl.allow("k", 3, 1800)
        fake.ttls.clear()
        rl.allow("k", 3, 1800)
        assert fake.ttls == {}

    def test_redis_down_fails_open(self):
        """Sign-in must survive a Redis blip - refusing everyone because the
        counter is unreachable would be a self-inflicted outage."""
        rl = RateLimiter(BrokenRedis())
        assert rl.allow("k", 1, 60)
        assert rl.allow("k", 1, 60)
