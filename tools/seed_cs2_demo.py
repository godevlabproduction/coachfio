"""Seed a sample Counter-Strike 2 match so cs.coachfio.com is not an empty site.

Two halves, and the difference matters:

  * The MATCH is real output. A replay export goes through the actual CS2 adapter
    (`ingest()`), so the score, rounds, kills and events are produced by shipped
    code exactly as a genuine upload would produce them.
  * The COACHING REPORT is written by hand. No model analysed anything, because
    there is nothing to analyse. It is a plausible report, not a real one, and it
    is labelled as a sample in `capture` so it can never be mistaken for output
    or swept up in a comparison of real analyses.

It also demonstrates a known problem rather than hiding it: the report renders
without the Attacking and Defensive sections, because those live in
core/pipeline/stages.py keyed to football fields (build_up_angles,
cdm_positioning). A CS2 report cannot fill them honestly, so it does not.

    docker compose run --rm api python -m tools.seed_cs2_demo <identity>
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import core.storage.db as db
from adapters.base.registry import load_builtin_adapters, registry
from core.models.domain import Insight, Match
from core.models.enums import MatchStatus, SourceType
from core.storage.repository import MatchRepository

SAMPLE_TAG = "sample_seeded"

REPLAY = {
    "teams": {"home": "Your team", "away": "Opponent"},
    "final_score": {"home": 11, "away": 13},
    "stats": {"home": {"kills": 78}, "away": {"kills": 85}},
    "rounds": [
        {"n": n, "t_ms": n * 105_000,
         "winner": "home" if n in (1, 2, 5, 8, 9, 12, 15, 18, 19, 21, 23) else "away",
         "highlights": (["bomb_planted"] if n in (2, 9, 15, 21) else []),
         "highlight_team": "home" if n in (2, 9, 15, 21) else "away"}
        for n in range(1, 25)
    ],
}

# Written by hand. Only the sections whose field names are game-neutral are
# filled; the football-specific ones are left out rather than stuffed.
REPORT = {
    "summary": (
        "You lost 13-11 on Mirage after leading 8-4. The opening pistol and force rounds "
        "were strong, but from round 13 you repeatedly took mid control without utility "
        "and lost the opener, which handed the opponent map control for free. Your aim "
        "held up all match; the round losses came from how you entered, not from duels."
    ),
    "match_context": {
        "mode": "Premier, Mirage",
        "result": "11-13 Loss",
        "score_by_phase": "8-4 up at the half, then 3-9 in the second half.",
        "technical_issues": "None observed.",
        "sample_quality": "Full demo, all 24 rounds present.",
        "confidence": "high - complete round-by-round data",
    },
    "diagnosis": {
        "biggest_strength": (
            "Opening duels on A. You won 7 of 9 first contacts there, and every one of "
            "them was taken with your crosshair already on the angle."
        ),
        "biggest_repeatable_mistake": (
            "Peeking mid without smoke or flash support. It happened in rounds 13, 14, "
            "17, 20 and 22, and you lost the opener in four of the five."
        ),
        "highest_value_habit": (
            "Do not take a mid duel until connector is smoked and someone is holding for "
            "the trade. One habit, five rounds of difference in this match alone."
        ),
        "main_tactical_problem": (
            "No default on eco and force rounds. Everyone drifted to the same side, so a "
            "single opponent holding an angle stopped the whole round."
        ),
        "main_mechanical_problem": (
            "Crosshair placement drops to the floor while repositioning, so the first "
            "shot after a rotate is a correction upward rather than a shot on target."
        ),
    },
    "event_log": [
        {"time": "13:15", "phase": "mid control", "severity": "high", "repeat_count": "5",
         "what_i_did": "Wide-peeked mid from T-ramp with no smoke and no trade behind you.",
         "why": "Wanting information early, but the peek gave it away instead of taking it.",
         "best_option": "Smoke connector first, then peek with a teammate holding for the trade.",
         "correction": "No mid duel until connector is smoked and a trade is in place."},
        {"time": "17:40", "phase": "post-plant", "severity": "medium", "repeat_count": "3",
         "what_i_did": "Held the bomb from an open angle instead of a cross position.",
         "why": "Habit of watching the bomb rather than the lane the defuser has to cross.",
         "best_option": "Hold from a cross angle so a retake has to clear two directions.",
         "correction": "Post-plant, take the angle that forces them to solve two problems."},
        {"time": "20:05", "phase": "eco", "severity": "medium", "repeat_count": "2",
         "what_i_did": "Full team stacked A on a force buy with no utility to open it.",
         "why": "Defaulting to the comfortable side rather than to the round type.",
         "best_option": "Split two and three, take map control, and play the numbers.",
         "correction": "On a force, take space first. Do not commit five to a held angle."},
    ],
    "practice_plan": [
        {"problem": "Crosshair drops while repositioning",
         "drill": "Aim_botz, 200 kills walking between bots without recentring downward",
         "reps": "10 minutes before every session",
         "success_metric": "First shot on target in 8 of 10 rotates",
         "common_mistake": "Practising standing still, which is not the failure case",
         "correction_phrase": "Head height, always"},
        {"problem": "Mid peeks without utility",
         "drill": "Five connector smokes from T-ramp until all five land first try",
         "reps": "3 sessions",
         "success_metric": "Smoke lands first attempt, 5 of 5",
         "common_mistake": "Learning the lineup but still peeking before it lands",
         "correction_phrase": "Smoke, then look"},
        {"problem": "No eco default",
         "drill": "Agree one force-round default with the team and run it every eco",
         "reps": "Next 5 matches",
         "success_metric": "Opener won on 3 of 5 forces",
         "common_mistake": "Changing the plan mid-round when the first contact goes badly",
         "correction_phrase": "Same default, every eco"},
    ],
    "next_video_test": {
        "match_type": "Premier, Mirage again so the comparison is like for like",
        "behaviour_to_practise": "No mid duel without a smoke and a trade partner",
        "behaviour_not_to_change": "Your A-site entries. They are the best part of your game.",
        "metrics_to_compare": "Opening duels won on mid, and rounds lost after losing the opener",
        "minimum_sample_size": "3 matches before drawing any conclusion",
    },
    "weakness_tags": [],
    "goals": [],
    "stats": {},
}


def main() -> None:
    identity = sys.argv[1] if len(sys.argv) > 1 else ""
    if not identity:
        raise SystemExit("usage: python -m tools.seed_cs2_demo <identity>")

    load_builtin_adapters()
    adapter = registry.get("cs2", "2")
    parsed = adapter.ingest(json.dumps(REPLAY).encode())

    db.init_db()
    with db.session_scope() as session:
        repo = MatchRepository(session)
        existing = [m for m in repo.list(game_id="cs2", edition="2", identity=identity)
                    if (m.capture or {}).get(SAMPLE_TAG)]
        if existing:
            print(f"sample already present ({existing[0].id[:8]}); nothing to do")
            return

        now = datetime.now(timezone.utc) - timedelta(hours=3)
        match = Match(
            game_id="cs2", game_edition="2",
            source_type=SourceType.REPLAY_FILE,
            status=MatchStatus.COMPLETE,
            capture={"identity": identity, "source": "seed", SAMPLE_TAG: True,
                     "player_side": "home", "skill_level": "intermediate"},
            outcome={"score": "11-13", "score_home": 11, "score_away": 13,
                     "result": "loss", "score_source": "replay_export"},
            metrics=parsed.metrics, events=parsed.events,
            parse_confidence=parsed.parse_confidence,
            cost_usd=0.0, created_at=now, updated_at=now,
        )
        match.insights = [Insight(
            scope="match", kind="coaching_report",
            summary=REPORT["summary"],
            payload={k: v for k, v in REPORT.items() if k != "summary"},
            model="hand-written sample", cost_usd=0.0,
        )]
        repo.save(match)
        print(f"seeded CS2 sample {match.id[:8]}  "
              f"{len(parsed.events)} events, {len(parsed.metrics)} metrics")


if __name__ == "__main__":
    main()
