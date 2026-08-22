"""The FC 26 shape of a coaching report.

This is football's vocabulary - half-spaces, cutbacks, centre-back movement,
jockeying, big chances - and it lives here rather than in core/pipeline/stages.py
so that the game-agnostic core stays that way. The core supplies the envelope
(summary, strengths, recurring mistakes, weakness tags, score, evidence ids) and
asks the adapter for everything below via `GameAdapter.report_spec()`.

Every string in the section schema is deliberately a STRING, including anything
that looks countable. Gemini samples the video at roughly 1fps, so it cannot
truly count touches or classify every pass; typed as integers it fills them with
confident invention, which is exactly how `goals_for` once ended up disagreeing
with the scoreboard. As strings the model can answer "not measurable from this
footage", and the instructions tell it to prefer that over a guess. The one
exception is `STATS`, which are whole-match totals the model reads off the HUD.
"""
from __future__ import annotations

from adapters.base.interface import ReportSpec

STATS = {  # stat key -> (label, higher_is_better)
    "shots": ("Shots", True),
    "big_chances": ("Big chances", True),
    "goals_for": ("Goals for", True),
    "goals_against": ("Goals against", False),
    "goals_conceded_from_crosses": ("Conceded from crosses", False),
    "defensive_errors": ("Defensive errors", False),
}

GOAL_ANALYSIS = {
    "type": "object",
    "properties": {
        "defender": {"type": "string"},      # your player most at fault / involved
        "what_happened": {"type": "string"},  # the sequence that led to the goal
        "root_cause": {"type": "string"},     # WHY it broke down (the real mistake)
        "fix": {"type": "string"},            # exact FC26 input / positioning fix
    },
    "required": ["what_happened", "root_cause", "fix"],
}

# EVIDENCE POLICY. Injected into every report, not left to the knowledge
# retriever: a rule that only applies when a keyword happens to match is not a
# rule. It exists because the coach's own question backlog was full of requests
# for hidden formulas ("exact stamina drain", "attribute threshold that triggers
# the badge") and for causes read off an icon above a player's head. Answering
# those confidently is how a coaching report becomes fiction. Full policy, with
# the terminology watchlist, lives in knowledge/policy.yaml.
_EVIDENCE_POLICY = (
    "EVIDENCE RULES - these override every section below.\n"
    "1. Separate observation from explanation from fact. An observation is what is "
    "visible in the footage; an explanation is the most plausible cause; a fact is a "
    "published rule. NEVER present an explanation as a fact.\n"
    "2. Never invent exact numbers. Stamina-drain rates, attribute thresholds, "
    "tackle-recovery times, coin or forfeit formulas, goalkeeper coverage radii, "
    "animation probabilities, Role++ activation requirements and AI positioning "
    "weights are NOT published. If asked or tempted, write 'the exact figure is not "
    "publicly verified' and coach the trade-off instead.\n"
    "3. An icon is not a mechanic. A badge, glow, overlay, celebration card or "
    "animation above a player does NOT prove a PlayStyle, Role, Evolution or tactic "
    "activated - it may be a UI marker, an Evolution cosmetic, a replay graphic or a "
    "recording artifact, and the player can switch some of them off. Describe the "
    "moment and coach the decision; do not attribute a hidden cause to it.\n"
    "4. Roles are movement tendencies, PlayStyles modify one action family. Never "
    "swap the two, and never say a Role guarantees a run.\n"
    "5. If a label cannot be confirmed as an FC 26 term, say so rather than reasoning "
    "from it - it may be a misread UI string, a community name or an OCR error.\n"
    "6. When the footage cannot answer it, say 'the supplied evidence is insufficient "
    "to identify the exact trigger' and give the player something testable instead.\n\n"
)

INSTRUCTIONS = (
    _EVIDENCE_POLICY
    + "REPORT STRUCTURE - fill EVERY section below. A section you genuinely cannot "
    "support from the video gets an honest 'not visible in this footage', never a "
    "plausible guess.\n"
    "- match_context: mode, both formations, result, score_by_phase (how the score "
    "moved through the match), technical_issues (lag/gameplay problems you can "
    "actually see), sample_quality (what this footage does and does not show) and "
    "confidence (high/medium/low + why).\n"
    "- diagnosis: the FIVE headline answers - biggest strength, biggest REPEATABLE "
    "mistake, the single habit with the highest improvement value, the main "
    "TACTICAL problem and the main MECHANICAL problem. Tactical = shape, roles, "
    "instructions. Mechanical = the input/technique itself.\n"
    "- event_log: the most important moments, AT MOST 12, ordered by time. For each: "
    "time, phase (build-up/attack/transition/defence/set piece), ball_location, "
    "selected_player, best_option (what was actually available), what_i_did, why it "
    "succeeded or failed, the exact correction, severity (low/medium/high) and "
    "repeat_count (how many times this same thing happened in the match).\n"
    "- attacking / defending: one or two sentences per field, each anchored to "
    "something you saw. 'not visible in this footage' where it was not.\n"
    "- elite_comparison: compare BEHAVIOURS, never inputs. Do not claim a "
    "professional uses a specific button unless this video proves it. Only compare "
    "against pros described in the knowledge above; list any pro you were asked "
    "about but hold no reference data for in reference_gaps. Give habits already "
    "shown, habits missing, and the SMALLEST realistic version to practise first.\n"
    "- practice_plan: EXACTLY three, ranked by value. Each needs problem, drill, "
    "reps (a number of repetitions or matches), success_metric (measurable), the "
    "common mistake when practising it, and a short correction_phrase to say to "
    "yourself.\n"
    "- tactical_changes: ONLY if this video PROVES the tactic contributed to the "
    "problem. If it does not, return an EMPTY list - that is the correct answer, "
    "and guessing here is actively harmful. When you do change something, give all "
    "of: current_setting, new_setting, problem_it_solves, the new_weakness_created, "
    "and reverse_when.\n"
    "- next_video_test: what to record next - match type, formation, ONE behaviour "
    "to practise, ONE behaviour NOT to change, the metrics to compare, and the "
    "minimum sample size.\n"
    "\n"
    "DO NOT REPEAT YOURSELF. Each section answers a DIFFERENT question, and they "
    "are read in order: the summary says what happened; the diagnosis names the "
    "single biggest cause; the event log gives the individual incidents; attacking "
    "and defending describe the PATTERN across those incidents; the practice plan "
    "says what to do about it. So do not restate the diagnosis inside "
    "attacking/defending, do not repeat an event_log entry as a bullet elsewhere, "
    "and do not describe one mistake three times in different words. If a section "
    "has nothing to add beyond what an earlier one said, write less rather than "
    "padding it. The result must read as ONE document, short enough to take in "
    "over a coffee - not five overlapping ones.\n"
)

# The flat "label: sentence" sections, declared ONCE as (key, heading, [(field,
# label)...]). Both the JSON schema below and every human rendering of these
# sections are derived from this, so a new field is one line here.
#
# core/report/pdf.py used to carry its own copy of this table - forty football
# field labels in a file whose docstring says no game may appear in it. The two
# could drift, and when they did the model answered a field, the API stored it,
# the web report showed it, and the PDF silently dropped it.
#
# Order here is DOCUMENT order (how the report reads). The schema is emitted in
# this order too.
KV_SECTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("match_context", "Match context", [
        ("mode", "Mode"),
        ("my_formation", "My formation"),
        ("opponent_formation", "Opponent formation"),
        ("result", "Result"),
        ("score_by_phase", "Score by phase"),          # "0-0 to 15', 1-2 by HT, 2-4 FT"
        ("technical_issues", "Technical issues"),      # connection / gameplay problems
        ("sample_quality", "Sample quality"),          # what the footage does not show
        ("confidence", "Confidence"),                  # high | medium | low + why
    ]),
    ("diagnosis", "Executive diagnosis", [
        ("biggest_strength", "Biggest strength"),
        ("biggest_repeatable_mistake", "Biggest repeatable mistake"),
        ("highest_value_habit", "Highest-value habit to fix"),
        ("main_tactical_problem", "Main tactical problem"),
        ("main_mechanical_problem", "Main mechanical problem"),
    ]),
    ("attacking", "Attacking analysis", [
        ("build_up_angles", "Build-up angles"),
        ("use_of_width", "Use of width"),
        ("half_space_occupation", "Half-spaces"),
        ("third_man_runs", "Third-man runs"),
        ("striker_movement", "Striker movement"),
        ("cam_movement", "CAM movement"),
        ("overlaps_underlaps", "Overlaps / underlaps"),
        ("cutback_creation", "Cutback creation"),
        ("shot_selection", "Shot selection"),
        ("rushes_final_action", "Rushing the final action"),
    ]),
    ("defending", "Defensive analysis", [
        ("shape", "Shape"),
        ("cdm_positioning", "CDM positioning"),
        ("centre_back_movement", "Centre-back movement"),
        ("player_switching", "Player switching"),
        ("jockey_and_sprint_usage", "Jockey / sprint usage"),
        ("pressing_angles", "Pressing angles"),
        ("through_ball_prevention", "Through-ball prevention"),
        ("cutback_prevention", "Cutback prevention"),
        ("recovery_after_losing_possession", "Recovery after losing it"),
        ("fullback_exposure", "Fullback exposure"),
    ]),
    ("next_video_test", "Next video to record", [
        ("match_type", "Match type"),
        ("formation", "Formation"),
        ("behaviour_to_practise", "Practise this"),
        ("behaviour_not_to_change", "Do NOT change this"),
        ("metrics_to_compare", "Compare these metrics"),
        ("minimum_sample_size", "Minimum sample"),
    ]),
]

# Where each flat section sits relative to the list/array sections, which have
# their own bespoke rendering and stay declared by hand below.
_SECTION_ORDER = ("match_context", "diagnosis", "event_log", "attacking", "defending",
                  "elite_comparison", "practice_plan", "tactical_changes", "next_video_test")


def _sections() -> dict:
    s = {"type": "string"}

    def obj(props: dict) -> dict:
        return {"type": "object", "properties": props}

    def arr(props: dict, required: list[str]) -> dict:
        return {"type": "array",
                "items": {"type": "object", "properties": props, "required": required}}

    flat = {key: obj({f: s for f, _ in fields}) for key, _, fields in KV_SECTIONS}

    built = {
        **flat,
        # One entry per important event. Capped in the prompt, not the schema:
        # an uncapped log on a 90-minute capture becomes the whole response.
        "event_log": arr({
            "time": s, "phase": s,        # build-up|attack|transition|defence|set piece
            "ball_location": s, "selected_player": s, "best_option": s,
            "what_i_did": s, "why": s, "correction": s,
            "severity": s,                # low | medium | high
            "repeat_count": s,            # how often this recurred in the match
        }, ["time", "phase", "what_i_did", "correction", "severity"]),
        "elite_comparison": obj({
            "habits_already_shown": {"type": "array", "items": s},
            "habits_missing": {"type": "array", "items": s},
            "smallest_next_step": s,
            "reference_gaps": s,          # pros we hold no reference data for
        }),
        # Exactly three, ranked. More than three is a wish list, not a plan.
        "practice_plan": arr({
            "problem": s, "drill": s, "reps": s, "success_metric": s,
            "common_mistake": s, "correction_phrase": s,
        }, ["problem", "drill", "success_metric"]),
        # May legitimately be empty: the instruction is to change nothing unless
        # the video PROVES the tactic contributed.
        "tactical_changes": arr({
            "current_setting": s, "new_setting": s, "problem_it_solves": s,
            "new_weakness_created": s, "reverse_when": s,
        }, ["current_setting", "new_setting", "problem_it_solves"]),
    }
    return {k: built[k] for k in _SECTION_ORDER}


def spec() -> ReportSpec:
    """The FC 26 report shape, handed to the core by the adapter."""
    return ReportSpec(sections=_sections(), instructions=INSTRUCTIONS,
                      stats=STATS, score_event=GOAL_ANALYSIS,
                      kv_sections=KV_SECTIONS)
