"""One-off, idempotent: make stored goal metrics match the scoreboard.

    docker compose run --rm api python -m tools._backfill_goal_metrics [--apply]

Runs a DRY RUN by default and prints what it would change. Pass --apply to write.

Why this exists. `goals_for` / `goals_against` used to be whatever the model
counted while watching, stored with `source=model` and a flat confidence of 0.6,
exactly like `shots`. But goals are the ONE stat we measure deterministically -
the scoreboard read gives the final score - so the model's count was a worse
answer to a question we already knew. On this database it was wrong once out of
seven: a 1-4 match stored `goals_for = 2`, and the Statistics page charted the 2.

New analyses now take these from the scoreboard (`_stats_to_metrics`). This
brings existing matches into line, so history and future agree.

Only EXISTING metric rows are corrected; nothing is created. A game that does not
record goals (CS2 counts rounds) is untouched.
"""
import sys

from core.models.domain import player_scoreline
from core.models.enums import MetricSource
from core.storage.db import session_scope
from core.storage.repository import MatchRepository

APPLY = "--apply" in sys.argv

with session_scope() as session:
    repo = MatchRepository(session)
    changed = 0
    for match in repo.list(limit=1000):
        if match.status.value != "complete":
            continue
        line = player_scoreline(match.outcome or {},
                                (match.capture or {}).get("player_side", "home"))
        if not line:
            continue
        gf, ga = (int(x) for x in line.split("-"))
        measured = {"goals_for": float(gf), "goals_against": float(ga)}

        touched = []
        for m in match.metrics:
            want = measured.get(m.key)
            if want is None:
                continue
            if m.value != want or m.source != MetricSource.DERIVED or m.confidence != 1.0:
                touched.append(f"{m.key}: {m.value} ({m.source.value}) -> {want} (derived)")
                m.value = want
                m.source = MetricSource.DERIVED
                m.confidence = 1.0
        if touched:
            changed += 1
            print(f"{str(match.created_at)[:10]}  {match.id[:8]}  score {line}")
            for t in touched:
                print(f"    {t}")
            if APPLY:
                repo.save(match)

    print(f"\n{'APPLIED to' if APPLY else 'WOULD change'} {changed} match(es)"
          + ("" if APPLY else " - re-run with --apply to write"))
