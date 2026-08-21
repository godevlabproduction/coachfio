"""Redis key/channel names for match progress. One place, imported by both
sides of the bus (the worker publishes, the API and watchdog read), and it
lives in core because both depend on core - never on each other."""
from __future__ import annotations


def channel_for(match_id: str) -> str:
    return f"match-progress:{match_id}"


def heartbeat_key(match_id: str) -> str:
    return f"match-heartbeat:{match_id}"
