"""Grab a few frames from a match's object storage and save them to /app/reports
so we can eyeball what the HUD/scoreboard actually shows.

    docker compose run --rm api python -m tools._grab_frames [match_id]
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

from core.storage.db import session_scope
from core.storage.frame_keys import frame_prefix, parse_frame_key
from core.storage.objectstore import get_object_store

OUT = Path("/app/reports/frames")


def latest_match_id() -> str:
    with session_scope() as s:
        r = s.execute(text("SELECT id FROM matches ORDER BY created_at DESC LIMIT 1")).first()
        return r[0]


def main() -> None:
    mid = sys.argv[1] if len(sys.argv) > 1 else latest_match_id()
    store = get_object_store()
    keys = []
    for k in store.list(frame_prefix(mid)):
        p = parse_frame_key(k)
        if p is not None:
            keys.append((p[0], k))  # (index, key)
    keys.sort()
    print(f"match {mid}: {len(keys)} frames")
    if not keys:
        return
    OUT.mkdir(parents=True, exist_ok=True)
    # Sample across the match: near start (post-kickoff), middle, and end.
    n = len(keys)
    picks = {int(n * f) for f in (0.15, 0.5, 0.75, 0.9, 0.98)}
    for i in sorted(picks):
        idx, key = keys[min(i, n - 1)]
        data = store.get(key)
        out = OUT / f"frame_{idx:05d}.jpg"
        out.write_bytes(data)
        print(f"WROTE {out}  (index {idx}, {len(data)} bytes)")


if __name__ == "__main__":
    main()
