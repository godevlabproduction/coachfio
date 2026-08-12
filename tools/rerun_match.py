"""Re-run the whole pipeline on a match's ALREADY-STORED frames (no re-upload),
using the current code + .env engine. Useful after an extraction fix.

    docker compose run --rm api python -m tools.rerun_match [match_id]

Defaults to the most recent match.
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from core.storage.db import session_scope
from workers.tasks import run_match_pipeline


def latest() -> str:
    with session_scope() as s:
        return s.execute(text("SELECT id FROM matches ORDER BY created_at DESC LIMIT 1")).scalar_one()


def main() -> None:
    mid = sys.argv[1] if len(sys.argv) > 1 else latest()
    print(f"re-running pipeline for match {mid} ...")
    result = run_match_pipeline(mid)
    print("RESULT:", result)


if __name__ == "__main__":
    main()
