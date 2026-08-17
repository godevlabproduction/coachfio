"""EA Sports FC 26 adapter - the ~10% code.

Everything declarative (identity, HUD schema, vocabulary, metrics, generic
validation) is in ./config/*.yaml and handled by ConfigDrivenAdapter. This file
only holds `interpret()` (turn raw OCR readings into metrics/outcome/events) and
one football-specific rule (a score can't decrease).
"""
from __future__ import annotations

import re
from pathlib import Path

from core.models.domain import Event, Metric
from core.models.enums import EventCategory, MetricSource
from adapters.base.config_adapter import ConfigDrivenAdapter
from adapters.base.interface import ParsedHud, RegionReading, ReportSpec

_CONFIG = Path(__file__).parent / "config"

# A single team's score in a real FC match effectively never exceeds this; higher
# reads are OCR noise (usually a crest/logo misread as digits).
_MAX_PLAUSIBLE_SCORE = 19
# A value must appear in at least this many frames to be accepted as a real score.
_MIN_SCORE_SUPPORT = 3
# Trailing window (in live frames) used to decide the FINAL score by majority -
# the end-of-match scoreboard state, robust to transient mid-match phantoms.
_SCORE_WINDOW = 15

# stat name (from region.meta) -> (metric_key template, unit, higher_is_better)
_STAT_TO_METRIC = {
    "possession": ("possession_{team}_pct", "%", True),
    "shots": ("shots_{team}", None, True),
    "shots_on_target": ("shots_on_target_{team}", None, True),
    "pass_accuracy": ("pass_accuracy_{team}_pct", "%", True),
    "tackles": ("tackles_{team}", None, True),
}


def _to_int(text: str) -> int | None:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def _clock_to_ms(text: str) -> int | None:
    m = re.search(r"(\d{1,3}):(\d{2})", text or "")
    if not m:
        return None
    return (int(m.group(1)) * 60 + int(m.group(2))) * 1000


class EaFc26Adapter(ConfigDrivenAdapter):
    config_dir = _CONFIG

    def coaching_playbook(self, hints: str = "") -> str:
        """FC 26 knowledge grounding (mechanics/tactics/remedies/meta). Lives in
        the adapter - core stays game-agnostic."""
        from adapters.ea_fc_26.knowledge_base import build_playbook
        return build_playbook(hints)

    def issue_vocabulary(self) -> list[dict]:
        from adapters.ea_fc_26.knowledge_base import issue_tags
        return issue_tags()

    def prompt_fragments(self, side: str = "home") -> dict[str, str]:
        """FC 26's voice in the model prompts. Lives in prompts.py."""
        from adapters.ea_fc_26.prompts import fragments
        return fragments(side)

    def report_spec(self) -> ReportSpec:
        """The football-shaped half of a coaching report. Lives in report.py -
        core supplies only the envelope."""
        from adapters.ea_fc_26.report import spec
        return spec()

    # --- competitive standing -> suggested coaching level ---------------------
    # Division Rivals runs Division 10 (lowest) up to Division 1, then Elite.
    # FUT Champions is a weekend bracket you qualify for; a player sitting in the
    # bottom divisions realistically isn't playing it, so that question is locked
    # for Divisions 7-10 rather than inviting a meaningless answer.
    _DIVISIONS = ["elite"] + [f"div{i}" for i in range(1, 11)]
    _NO_CHAMPS_DIVISIONS = {"div7", "div8", "div9", "div10"}

    def skill_survey(self) -> list[dict]:
        def div_label(v: str) -> str:
            return "Elite Division" if v == "elite" else "Division " + v[3:]

        return [
            {
                "key": "division",
                "label": "Division Rivals",
                "help": "Where you normally sit, not your best ever week.",
                "options": [{"value": v, "label": div_label(v)} for v in self._DIVISIONS],
            },
            {
                "key": "champs_wins",
                "label": "FUT Champions wins",
                "help": "Your usual number of wins out of 15.",
                "options": [
                    {"value": "1-4", "label": "1-4 wins"},
                    {"value": "5-8", "label": "5-8 wins"},
                    {"value": "9-12", "label": "9-12 wins"},
                    {"value": "13-15", "label": "13-15 wins"},
                ],
                "locked_by": {
                    "key": "division",
                    "values": sorted(self._NO_CHAMPS_DIVISIONS),
                    "reason": "Set a division above 7 to record a Champs result.",
                },
            },
        ]

    def suggest_skill_level(self, answers: dict) -> dict | None:
        """Division sets the floor; a strong Champs record can only raise it.

        Deliberately one-directional: a bad weekend says little (illness, off-meta
        squad, playing tired), whereas 13 wins is hard to achieve by accident.
        """
        division = str((answers or {}).get("division") or "").strip().lower()
        if division not in self._DIVISIONS:
            return None

        if division == "elite" or division in ("div1", "div2"):
            level, reason = "pro", f"{'Elite Division' if division == 'elite' else 'Division ' + division[3:]} is competitive football"
        elif division in ("div3", "div4", "div5", "div6"):
            level, reason = "intermediate", f"Division {division[3:]} players know the fundamentals"
        else:
            level, reason = "amateur", f"Division {division[3:]} is where the basics still decide games"

        wins = str((answers or {}).get("champs_wins") or "").strip()
        if division not in self._NO_CHAMPS_DIVISIONS and wins:
            rank = {"amateur": 0, "intermediate": 1, "pro": 2}
            if wins == "13-15":
                bumped, why = "pro", "13+ Champs wins is elite finishing"
            elif wins == "9-12":
                bumped, why = ("pro" if division in ("elite", "div1", "div2", "div3", "div4")
                               else "intermediate"), "9-12 Champs wins is a strong record"
            elif wins == "5-8":
                bumped, why = "intermediate", "5-8 Champs wins means the fundamentals are there"
            else:
                bumped, why = level, ""
            if rank[bumped] > rank[level]:
                level, reason = bumped, why

        return {"level": level, "reason": reason}

    def name_badge_regions(self) -> dict | None:
        # FC-Pro broadcast overlay: on-ball name badges sit at the bottom corners.
        # LEFT badge = HOME team's player, RIGHT badge = AWAY team's. Calibrated
        # against 1280x720 broadcast frames (normalized). Widened for OCR margin.
        return {
            "home": (0.045, 0.885, 0.20, 0.055),
            "away": (0.775, 0.885, 0.20, 0.055),
        }

    def interpret(self, readings: list[RegionReading]) -> ParsedHud:
        parsed = ParsedHud()
        metrics: list[Metric] = []
        events: list[Event] = []

        stat_readings = [r for r in readings if r.is_stat_screen]
        match_readings = [r for r in readings if not r.is_stat_screen]

        # --- 0. Which frames are actually LIVE PLAY? --------------------------
        # Replays, goal cutscenes and menus inject junk into the fixed HUD regions
        # (a jersey number read as a confident "8", etc.). The live match clock is
        # only shown during live play, so a frame whose CLOCK region parses as a
        # valid MM:SS is a strong, model-free signal that it's real gameplay. We
        # only trust score reads from those frames. (Stage 2's scene classifier
        # will do this more precisely in Phase 1.)
        #
        # Crucially the clock must be RUNNING (> 0:00): a pre-kickoff intro or a
        # paused/recap overlay can sit at 00:00 while the score graphic animates
        # (we saw an intro tick the score 1->8 at 00:00, producing phantom "goals"
        # before kickoff). A real goal is only ever scored with the clock running.
        live_frames = {
            r.frame_index
            for r in match_readings
            if r.region.meta.get("role") == "clock"
            and r.confidence >= 0.5
            and (_clock_to_ms(r.text) or 0) > 0
        }

        # --- 1. Score over time -> final score + goal/concede events ----------
        # A score is "what the scoreboard shows at the END of the match", NOT the
        # highest number ever read. OCR of a single frame is noisy - a set-piece
        # sequence, a crest beside the digit, or a downscaled frame can produce a
        # confident phantom (e.g. an "8" held for ~6 frames while the true score
        # is 1). Taking max(confirmed) lets any such phantom win permanently.
        #
        # Robust rule: the FINAL score is the majority reading over the trailing
        # window of live frames (the real ending dominates; a mid-match phantom
        # that reverts never reaches the end). Events are emitted only for
        # increments up to that final, so out-of-range phantoms are dropped.
        final_score = {"home": 0, "away": 0}
        confs: list[float] = []
        for team in ("home", "away"):
            # Ordered live reads: (frame_index, value, confidence, timestamp_ms).
            reads: list[tuple[int, int, float, int]] = []
            for r in match_readings:
                if r.region.meta.get("role") != "score" or r.region.meta.get("team") != team:
                    continue
                # Gate on live play - but if the clock never read (unusual), fall
                # back to using all frames rather than returning nothing.
                if live_frames and r.frame_index not in live_frames:
                    continue
                val = _to_int(r.text)
                if val is None or not (0 <= val <= _MAX_PLAUSIBLE_SCORE) or r.confidence < 0.5:
                    continue
                reads.append((r.frame_index, val, r.confidence, r.timestamp_ms))

            if not reads:
                continue
            reads.sort(key=lambda x: x[0])

            # Final score = majority over the trailing window (end-of-match state).
            window = reads[-max(_SCORE_WINDOW, len(reads) // 4):]
            tallies: dict[int, int] = {}
            for _, v, _c, _ts in window:
                tallies[v] = tallies.get(v, 0) + 1
            # Most frequent wins; ties broken toward the higher score.
            final_score[team] = max(tallies, key=lambda v: (tallies[v], v))

            # Confirmed increments across the whole match, capped at the final -
            # this is what turns into goal/concede events. A value must clear the
            # corroboration bar AND be consistent with the final score.
            by_val: dict[int, list[float]] = {}
            first_ts: dict[int, int] = {}
            for _fi, v, c, ts in reads:
                by_val.setdefault(v, []).append(c)
                first_ts[v] = min(first_ts.get(v, ts), ts)
            confirmed = {
                v: cs for v, cs in by_val.items()
                if len(cs) >= _MIN_SCORE_SUPPORT and 0 < v <= final_score[team]
            }
            confs.extend(c for cs in confirmed.values() for c in cs)

            prev = 0
            for v in sorted(confirmed):
                if v > prev:
                    events.append(
                        Event(
                            timestamp_ms=first_ts[v],
                            category=EventCategory.SCORE_CHANGE,
                            game_event_type="goal" if team == "home" else "concede",
                            confidence=sum(confirmed[v]) / len(confirmed[v]),
                            payload={"team": team, "score": v},
                        )
                    )
                    prev = v

        # --- 2. Match-facts stat screen -> metrics ---------------------------
        got_stat_screen = False
        for r in stat_readings:
            meta = r.region.meta
            if meta.get("role") == "title":
                got_stat_screen = True
                events.append(
                    Event(
                        timestamp_ms=r.timestamp_ms,
                        category=EventCategory.STAT_SNAPSHOT,
                        game_event_type="stat_screen",
                        confidence=r.confidence,
                        frame_refs=[str(r.frame_index)],
                    )
                )
            if meta.get("role") != "stat":
                continue
            stat, team = meta.get("stat"), meta.get("team")
            tmpl = _STAT_TO_METRIC.get(stat)
            if not tmpl or team not in ("home", "away"):
                continue
            value = _to_int(r.text)
            if value is None:
                continue
            key_tmpl, unit, hib = tmpl
            confs.append(r.confidence)
            metrics.append(
                Metric(
                    key=key_tmpl.format(team=team),
                    label=f"{stat.replace('_', ' ').title()} ({team})",
                    value=float(value),
                    unit=unit,
                    higher_is_better=hib,
                    source=MetricSource.OCR,
                    confidence=r.confidence,
                    extra={"raw": r.text, "frame_index": r.frame_index},
                )
            )

        # --- 3. Score metrics + outcome --------------------------------------
        for team in ("home", "away"):
            metrics.append(
                Metric(
                    key=f"score_{team}",
                    label=f"Goals ({team})",
                    value=float(final_score[team]),
                    higher_is_better=(team == "home"),
                    source=MetricSource.OCR,
                    confidence=(sum(confs) / len(confs)) if confs else 0.0,
                )
            )
        h, a = final_score["home"], final_score["away"]
        parsed.outcome = {
            "score": f"{h}-{a}",
            "score_home": h,
            "score_away": a,
            "result": "win" if h > a else "loss" if h < a else "draw",
            "stat_screen_found": got_stat_screen,
        }

        parsed.metrics = metrics
        parsed.events = sorted(events, key=lambda e: e.timestamp_ms)
        parsed.parse_confidence = round(sum(confs) / len(confs), 3) if confs else 0.0
        if not got_stat_screen:
            parsed.warnings.append("No match-facts/full-time screen detected; stats incomplete.")
        parsed.warnings.extend(self.validate(parsed))
        return parsed

    def validate(self, parsed: ParsedHud) -> list[str]:
        # Generic range checks from game.yaml…
        warnings = super().validate(parsed)
        # …plus one football-specific temporal rule: score events must be
        # non-decreasing per team (guards against an OCR misread inflating then
        # "dropping" the score).
        for team in ("home", "away"):
            vals = [
                e.payload.get("score")
                for e in parsed.events
                if e.category == EventCategory.SCORE_CHANGE and e.payload.get("team") == team
            ]
            if any(b is not None and a is not None and b < a for a, b in zip(vals, vals[1:])):
                warnings.append(f"Score for {team} decreased between snapshots - likely OCR misread.")
        return warnings
