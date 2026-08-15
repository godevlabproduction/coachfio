"""Object-store key convention for uploaded frames.

The frame index and capture timestamp are encoded in the key itself, so the
worker can reconstruct the ordered, timestamped frame list purely by listing the
prefix - no manifest, no append races during parallel upload."""
from __future__ import annotations

import re

_PREFIX = "matches/{match_id}/frames/"
_NAME = "{index:06d}_{timestamp_ms}.jpg"
_PARSE = re.compile(r"(?P<index>\d+)_(?P<ts>\d+)\.jpg$")


def frame_prefix(match_id: str) -> str:
    return _PREFIX.format(match_id=match_id)


def source_key(match_id: str) -> str:
    """Object key for a non-video source (replay file / API payload)."""
    return f"matches/{match_id}/source"


def frame_key(match_id: str, index: int, timestamp_ms: int) -> str:
    return frame_prefix(match_id) + _NAME.format(index=index, timestamp_ms=timestamp_ms)


def parse_frame_key(key: str) -> tuple[int, int] | None:
    """Return (index, timestamp_ms) or None if the key isn't a frame key."""
    m = _PARSE.search(key)
    if not m:
        return None
    return int(m.group("index")), int(m.group("ts"))
