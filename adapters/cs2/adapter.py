"""Counter-Strike 2 adapter - Phase 2 proof that a NON-VIDEO game is a plugin.

Source is a replay/stats export (JSON), so there's no HUD and no OCR: the ~10%
of code here is `ingest()`, which turns the structured export into the same
Match / Event / Metric the core already understands. Identity, vocabulary, and
metric definitions are declared in ./config/game.yaml (no hud.yaml).

Expected export shape (a documented, parseable format):
    {
      "teams": {"home": "NAVI", "away": "FaZe"},
      "final_score": {"home": 13, "away": 11},
      "stats": {"home": {"kills": 85}, "away": {"kills": 80}},
      "rounds": [
        {"n": 1, "t_ms": 95000, "winner": "home",
         "highlights": ["bomb_planted"], "highlight_team": "home"},
        ...
      ]
    }
"""
from __future__ import annotations

import json
from pathlib import Path

from core.models.domain import Event, Metric
from core.models.enums import EventCategory, MetricSource
from adapters.base.config_adapter import ConfigDrivenAdapter
from adapters.base.interface import ParsedHud

_CONFIG = Path(__file__).parent / "config"


class Cs2Adapter(ConfigDrivenAdapter):
    config_dir = _CONFIG

    def ingest(self, source: bytes) -> ParsedHud:
        data = json.loads(source.decode("utf-8"))
        parsed = ParsedHud(parse_confidence=1.0)  # structured export = exact

        fs = data.get("final_score", {})
        h, a = int(fs.get("home", 0)), int(fs.get("away", 0))
        stats = data.get("stats", {})

        def stat(side: str, key: str) -> float:
            return float(stats.get(side, {}).get(key, 0))

        parsed.metrics = [
            Metric(key="score_home", label="Rounds (home)", value=float(h),
                   higher_is_better=True, source=MetricSource.DERIVED),
            Metric(key="score_away", label="Rounds (away)", value=float(a),
                   higher_is_better=False, source=MetricSource.DERIVED),
            Metric(key="kills_home", label="Kills (home)", value=stat("home", "kills"),
                   higher_is_better=True, source=MetricSource.DERIVED),
            Metric(key="kills_away", label="Kills (away)", value=stat("away", "kills"),
                   higher_is_better=False, source=MetricSource.DERIVED),
        ]

        vocab = self.event_type_map()
        events: list[Event] = []
        for r in data.get("rounds", []):
            ts = int(r.get("t_ms", 0))
            events.append(
                Event(
                    timestamp_ms=ts,
                    category=EventCategory.SCORE_CHANGE,
                    game_event_type="round_win",
                    confidence=1.0,
                    payload={"team": r.get("winner"), "round": r.get("n")},
                )
            )
            for hl in r.get("highlights", []):
                etd = vocab.get(hl)
                if etd:
                    events.append(
                        Event(
                            timestamp_ms=ts,
                            category=etd.category,
                            game_event_type=hl,
                            confidence=1.0,
                            payload={"team": r.get("highlight_team"), "round": r.get("n")},
                        )
                    )

        parsed.events = sorted(events, key=lambda e: e.timestamp_ms)
        parsed.outcome = {
            "score": f"{h}-{a}",
            "score_home": h,
            "score_away": a,
            "result": "win" if h > a else "loss" if h < a else "draw",
            "teams": data.get("teams", {}),
        }
        parsed.warnings.extend(self.validate(parsed))
        return parsed
