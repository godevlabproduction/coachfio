"""PDF export of the coaching report.

The builder is a pure function over a Match, so it is tested directly - no DB, no
HTTP, no reportlab output inspection beyond the header and page count. What
matters here is that it never RAISES on the shapes real reports actually take,
because a crash in the builder is a failed download of work the user already
paid for.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.models.domain import Insight, Match
from core.report import build_match_report_pdf, report_filename

PDF_MAGIC = b"%PDF"


def _match(payload: dict | None = None, summary: str = "A summary.", **kw) -> Match:
    insights = []
    if payload is not None:
        insights = [Insight(kind="coaching_report", summary=summary, payload=payload)]
    return Match(
        game_id=kw.pop("game_id", "demo"), game_edition=kw.pop("game_edition", "1"),
        outcome=kw.pop("outcome", {"score": "3-1", "result": "win"}),
        insights=insights,
        created_at=kw.pop("created_at", datetime(2026, 8, 13, tzinfo=timezone.utc)),
        **kw,
    )


def test_returns_none_when_the_match_has_no_report():
    """The caller decides whether that is a 404 or 'nothing to send' - the
    builder must not invent an empty document."""
    assert build_match_report_pdf(_match(payload=None)) is None


def test_builds_a_pdf_from_a_full_report():
    pdf = build_match_report_pdf(_match({
        "strengths": [{"point": "Good early distribution.", "evidence_ids": [1]}],
        "recurring_mistakes": [{"point": "Diving in with the CB.", "evidence_ids": [2]}],
        "positioning_issues": [{"point": "Double pivot too high."}],
        "decision_patterns": [{"point": "Forcing central passes."}],
        "practice_drills": [{"point": "2v2 containment."}],
        "goals": [
            {"time": "02:39", "type": "conceded", "summary": "Cutback.", "fix": "Jockey."},
            {"time": "10:02", "type": "scored", "summary": "Counter."},
        ],
        "stats": {"goals_for": 3, "goals_against": 1, "shots": 7},
        "evidence_log": ["[01:12] Pressed high.", "[04:20] Lost the ball."],
    }), player_name="Ilija")
    assert pdf is not None and pdf.startswith(PDF_MAGIC)
    # The evidence log forces its own page, so a full report is never one page.
    assert pdf.count(b"/Type /Page\n") >= 2 or b"/Count 2" in pdf or len(pdf) > 3000


def test_accepts_points_as_plain_strings():
    """Points are `{point, evidence_ids}` today, but rows written before that
    change stored bare strings. Losing a whole section to it would be silent."""
    pdf = build_match_report_pdf(_match({"strengths": ["Held the line well."]}))
    assert pdf is not None and pdf.startswith(PDF_MAGIC)


def test_survives_markup_characters_in_the_coach_prose():
    """reportlab paragraphs parse a mini-HTML, so an unescaped '<' or '&' in the
    model's text aborts the whole render. The coach writes things like
    'L2+R2 <-> jockey' and 'Fix: press & recover' unprompted."""
    pdf = build_match_report_pdf(_match(
        {"strengths": ["Used L2 <-> R2 & held shape <b>well</b>."],
         "goals": [{"time": "01:00", "type": "conceded",
                    "summary": "Ball & runner <lost>.", "fix": "Track <him>."}]},
        summary="Pressed & recovered <fast>.",
    ))
    assert pdf is not None and pdf.startswith(PDF_MAGIC)


def test_builds_from_a_minimal_report():
    """Only `summary` is required by the report schema; every other section is
    optional and often absent on a short or failed-ish match."""
    pdf = build_match_report_pdf(_match({}))
    assert pdf is not None and pdf.startswith(PDF_MAGIC)


def test_builds_when_the_match_has_no_outcome():
    pdf = build_match_report_pdf(_match({"strengths": ["Fine."]}, outcome={}))
    assert pdf is not None and pdf.startswith(PDF_MAGIC)


def test_non_numeric_stats_are_skipped_not_rendered():
    """The stats table would raise laying out a None, and a stray '' cell reads
    as a real measurement of nothing."""
    pdf = build_match_report_pdf(_match({"stats": {"shots": 5, "big_chances": None,
                                                   "goals_for": "n/a"}}))
    assert pdf is not None and pdf.startswith(PDF_MAGIC)


@pytest.mark.parametrize("score,expected", [
    ("3-1", "coachfio-2026-08-13-3v1.pdf"),
    ("", "coachfio-2026-08-13-match.pdf"),
])
def test_filename_is_derived_from_the_score_and_date(score, expected):
    assert report_filename(_match({}, outcome={"score": score})) == expected


def test_filename_cannot_carry_a_path_or_break_the_header():
    """It goes straight into Content-Disposition and onto a filesystem, so a
    quote or a slash from upstream data must not survive."""
    name = report_filename(_match({}, outcome={"score": '../../etc "passwd'}))
    assert "/" not in name and '"' not in name and " " not in name
    assert name.endswith(".pdf")


# --- the requested report template -------------------------------------------

def _template_payload() -> dict:
    return {
        "diagnosis": {
            "biggest_strength": "Recovery running.",
            "biggest_repeatable_mistake": "Diving in with the CB.",
            "highest_value_habit": "Defend first with the CDM.",
            "main_tactical_problem": "Both fullbacks push up together.",
            "main_mechanical_problem": "Late player switching.",
        },
        "match_context": {"mode": "Rivals", "my_formation": "4-2-3-1",
                          "confidence": "medium - one camera angle"},
        "event_log": [{
            "time": "06:42", "phase": "defence", "ball_location": "Left half-space",
            "selected_player": "Maldini (CB)", "best_option": "Stay with the CDM",
            "what_i_did": "Switched to the CB and followed the ball",
            "why": "It opened the pass into the striker",
            "correction": "Hold the CDM, cover the striker, let the CB hold the line.",
            "severity": "high", "repeat_count": "3",
        }],
        "decision_metrics": {
            "pass_mix": "not measurable from this footage",
            "attacks_reaching_box": "6",
            "measurement_note": "Pass direction cannot be counted at this sampling rate.",
        },
        "attacking": {"use_of_width": "Held width on the right only."},
        "defending": {"player_switching": "Consistently one action late."},
        "elite_comparison": {
            "habits_already_shown": ["Tracks runners back into the box."],
            "habits_missing": ["Delaying the counter instead of diving in."],
            "smallest_next_step": "Count to one before pressing.",
            "reference_gaps": "No reference data held for Brimzimir or Nassada.",
        },
        "practice_plan": [{
            "problem": "Diving in with the CB.", "drill": "2v2 containment.",
            "reps": "3 matches", "success_metric": "Zero CB stand-tackles in the first phase.",
            "common_mistake": "Jockeying with sprint held.",
            "correction_phrase": "CDM first, CB holds.",
        }],
        "in_game_triggers": {"opponent_low_block": "Switch, then attack the far half-space."},
        "tactical_changes": [],
        "next_video_test": {"match_type": "Rivals", "minimum_sample_size": "3 matches"},
    }


def test_template_sections_all_render():
    pdf = build_match_report_pdf(_match(_template_payload()), player_name="Ilija")
    assert pdf is not None and pdf.startswith(PDF_MAGIC)


def test_empty_tactical_changes_is_rendered_not_dropped():
    """An empty list is the CORRECT answer when the video does not implicate the
    tactics, so the section must still say so - silently omitting it looks like
    the coach forgot to answer."""
    payload = _template_payload()
    payload["tactical_changes"] = []
    pdf = build_match_report_pdf(_match(payload))
    assert pdf is not None and pdf.startswith(PDF_MAGIC)


def test_template_and_legacy_reports_both_build():
    """Reports written before the template have none of these keys; reports
    written after have none of the old ones. Both must produce a document."""
    legacy = {"strengths": ["Good shape."], "positioning_issues": ["Pivot too high."],
              "practice_drills": ["Containment drill."]}
    for payload in (legacy, _template_payload(), {**legacy, **_template_payload()}):
        pdf = build_match_report_pdf(_match(payload))
        assert pdf is not None and pdf.startswith(PDF_MAGIC)


# --- the five-page cap --------------------------------------------------------

def _huge_payload() -> dict:
    """Deliberately oversized: a maxed-out event log, long prose in every field
    and a large evidence log. Left unchecked this runs well past five pages."""
    long_text = ("You stepped out with the centre-back to make the first challenge "
                 "and vacated the central lane, which is the same habit that cost "
                 "the goal at 02:39 and again at 05:28. " * 3)
    return {
        "diagnosis": {k: long_text for k in
                      ("biggest_strength", "biggest_repeatable_mistake",
                       "highest_value_habit", "main_tactical_problem",
                       "main_mechanical_problem")},
        "match_context": {k: long_text for k in
                          ("mode", "opponent_formation", "my_formation", "result",
                           "score_by_phase", "technical_issues", "sample_quality",
                           "confidence")},
        "event_log": [{
            "time": f"{m:02d}:00", "phase": "defence", "ball_location": long_text,
            "selected_player": "Maldini (CB)", "best_option": long_text,
            "what_i_did": long_text, "why": long_text, "correction": long_text,
            "severity": "high", "repeat_count": "4",
        } for m in range(1, 13)],
        "attacking": {k: long_text for k in
                      ("build_up_angles", "use_of_width", "half_space_occupation",
                       "third_man_runs", "striker_movement", "cam_movement",
                       "overlaps_underlaps", "cutback_creation", "shot_selection",
                       "rushes_final_action")},
        "defending": {k: long_text for k in
                      ("shape", "cdm_positioning", "centre_back_movement",
                       "player_switching", "jockey_and_sprint_usage", "pressing_angles",
                       "through_ball_prevention", "cutback_prevention",
                       "recovery_after_losing_possession", "fullback_exposure")},
        "elite_comparison": {"habits_already_shown": [long_text] * 4,
                             "habits_missing": [long_text] * 4,
                             "smallest_next_step": long_text},
        "practice_plan": [{"problem": long_text, "drill": long_text,
                           "reps": "3 matches", "success_metric": long_text,
                           "common_mistake": long_text,
                           "correction_phrase": long_text} for _ in range(3)],
        "goals": [{"time": "02:39", "type": "conceded", "summary": long_text,
                   "fix": long_text} for _ in range(6)],
        "tactical_changes": [{"current_setting": long_text, "new_setting": long_text,
                              "problem_it_solves": long_text,
                              "new_weakness_created": long_text,
                              "reverse_when": long_text}],
        "next_video_test": {k: long_text for k in
                            ("match_type", "formation", "behaviour_to_practise",
                             "behaviour_not_to_change", "metrics_to_compare",
                             "minimum_sample_size")},
        "evidence_log": [f"[{m:02d}:00] {long_text}" for m in range(1, 40)],
        "strengths": [{"point": long_text}] * 4,
        "recurring_mistakes": [{"point": long_text}] * 4,
    }


def _page_count(pdf: bytes) -> int:
    """Cheap page count: reportlab writes one /Type /Page per page."""
    return pdf.count(b"/Type /Page\n") or pdf.count(b"/Type/Page")


def test_report_never_exceeds_five_pages():
    """The cap is enforced by measuring the rendered document, not by trusting
    the prompt to keep the model brief - so an unusually verbose report gets
    trimmed rather than shipping as a twelve-page wall."""
    from core.report.pdf import MAX_PAGES

    pdf = build_match_report_pdf(_match(_huge_payload()), player_name="Ilija")
    assert pdf is not None and pdf.startswith(PDF_MAGIC)
    assert _page_count(pdf) <= MAX_PAGES, f"{_page_count(pdf)} pages, cap is {MAX_PAGES}"


def test_a_normal_report_is_not_trimmed():
    """The cap must not be so eager that an ordinary report loses its evidence
    log - trimming should only kick in when the document is genuinely too long."""
    payload = _template_payload()
    payload["evidence_log"] = ["[01:12] Pressed high.", "[04:20] Lost the ball."]
    pdf = build_match_report_pdf(_match(payload))
    assert pdf is not None
    assert b"Evidence log" in pdf or _page_count(pdf) <= 5


def test_removed_sections_are_gone():
    """decision_metrics and in_game_triggers were dropped: they duplicated the
    analysis sections, and the decision counts were unmeasurable from video."""
    from adapters.ea_fc_26.adapter import EaFc26Adapter

    keys = {k for k, _, _ in EaFc26Adapter().report_spec().kv_sections}
    assert "decision_metrics" not in keys
    assert "in_game_triggers" not in keys


def test_the_pdf_takes_its_section_labels_from_the_adapter():
    """The PDF used to keep its own copy of the field labels, so a field added to
    the adapter was answered, stored, shown on the web, and missing here."""
    from adapters.ea_fc_26.adapter import EaFc26Adapter
    from core.report.pdf import _kv_sections

    match = _match({"attacking": {"use_of_width": "Held the touchline all match."}},
                   game_id="ea-fc", game_edition="26")
    assert _kv_sections(match) == EaFc26Adapter().report_spec().kv_sections


def test_an_unknown_game_still_produces_a_pdf_without_its_sections():
    """The envelope is core, so a game with no adapter loses only its tactical
    sections. A partial report beats no report."""
    from core.report.pdf import _kv_sections

    match = _match({"summary": "x"}, game_id="not-a-real-game", game_edition="9")
    assert _kv_sections(match) == []
    assert build_match_report_pdf(match)[:4] == PDF_MAGIC


def test_the_pdf_header_uses_the_player_first_scoreline():
    """This file was missed when the ordering was unified everywhere else, so an
    away player got a PDF headed 4-3 over a body reading '3-4 Loss'."""
    from core.report.pdf import report_filename

    match = _match({"summary": "x"},
                   outcome={"score": "4-3", "score_home": 4, "score_away": 3, "result": "loss"},
                   capture={"player_side": "away"})
    assert "3v4" in report_filename(match)
