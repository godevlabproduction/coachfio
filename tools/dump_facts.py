"""Show the grounding facts + events + segment spans for the newest match, so we
can see WHAT the coaching model was actually told (to trace a confabulation).

Usage: docker compose run --rm api python -m tools.dump_facts
"""
from __future__ import annotations

from sqlalchemy import text

from core.storage.db import session_scope


def main() -> None:
    with session_scope() as session:
        m = session.execute(
            text("SELECT id, created_at, outcome, insights FROM matches "
                 "ORDER BY created_at DESC LIMIT 1")
        ).mappings().first()
        if not m:
            print("(no matches)")
            return
        mid = m["id"]
        print(f"match id   : {mid}")
        print(f"created_at : {m['created_at']}")
        print(f"outcome    : {m['outcome']}")

        evs = session.execute(
            text("SELECT timestamp_ms, category, game_event_type FROM match_events "
                 "WHERE match_id = :mid ORDER BY timestamp_ms"),
            {"mid": mid},
        ).mappings().all()

    print(f"\nEVENTS ({len(evs)}):")
    for e in evs:
        secs = (e["timestamp_ms"] or 0) // 1000
        print(f"  {secs // 60:02d}:{secs % 60:02d}  {e['category']:16s} {e['game_event_type']}")
    score_changes = [e for e in evs if str(e["category"]).endswith("SCORE_CHANGE")
                     or e["category"] == "score_change"]
    print(f"\nSCORE_CHANGE events: {len(score_changes)}")


if __name__ == "__main__":
    main()
