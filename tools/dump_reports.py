"""Dump the coaching_report insight for the N most recent matches.

Usage: docker compose run --rm api python -m tools.dump_reports [N]
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from core.storage.db import session_scope


def main(n: int = 2) -> None:
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT id, created_at, game_id, status, "
                "outcome, parse_confidence, cost_usd, insights "
                "FROM matches ORDER BY created_at DESC LIMIT :n"
            ),
            {"n": n},
        ).mappings().all()

    if not rows:
        print("(no matches in the database)")
        return

    for i, r in enumerate(rows):
        print("=" * 78)
        print(f"MATCH #{i} (newest first)")
        print(f"  id            : {r['id']}")
        print(f"  created_at    : {r['created_at']}")
        print(f"  game          : {r['game_id']}")
        print(f"  status        : {r['status']}")
        print(f"  outcome       : {r['outcome']}")
        print(f"  parse_conf    : {r['parse_confidence']}")
        print(f"  cost_usd      : {r['cost_usd']}")

        insights = r["insights"] or []
        reports = [x for x in insights if x.get("kind") == "coaching_report"]
        if not reports:
            print("  coaching_report: (none)")
            print(f"  (insights present: {[x.get('kind') for x in insights]})")
            continue

        for rep in reports:
            p = rep.get("payload", {})
            print("  --- COACHING REPORT ---")
            print(f"  model         : {rep.get('model')}")
            print(f"  cost_usd      : {rep.get('cost_usd')}")
            print(f"  player_side   : {p.get('player_side')}")
            print(f"  frames_reviewed: {p.get('frames_reviewed')}   segments_read: {p.get('segments_read')}")
            print(f"\n  SUMMARY:\n    {rep.get('summary')}")
            for title, key in (
                ("RECURRING MISTAKES", "recurring_mistakes"),
                ("POSITIONING ISSUES", "positioning_issues"),
                ("DECISION-MAKING PATTERNS", "decision_patterns"),
                ("WHAT TO PRACTICE", "practice_drills"),
            ):
                items = p.get(key) or []
                print(f"\n  {title}:")
                if not items:
                    print("    (none)")
                for it in items:
                    print(f"    - {it}")
        print()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2)
