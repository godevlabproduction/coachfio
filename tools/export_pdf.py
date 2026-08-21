"""Export the latest coaching_report to a nicely formatted PDF.

    docker compose run --rm api sh -c "pip install -q fpdf2 && python -m tools.export_pdf"

Writes to /app/reports/<id>.pdf which is the host project's ./reports folder.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from core.storage.db import session_scope
from core.models.domain import player_scoreline

OUT_DIR = Path("/app/reports")

# fpdf2 core fonts are latin-1; map the few unicode chars our text may carry.
_SUBS = {
    "–": "-", "—": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "•": "-", " ": " ",
}


def _latin1(s: str) -> str:
    s = str(s or "")
    for k, v in _SUBS.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def _report_for(match_id: str | None):
    """A specific match's report, or the most recent match that HAS one."""
    with session_scope() as session:
        if match_id:
            rows = session.execute(
                text("SELECT id, created_at, game_id, outcome, parse_confidence, "
                     "cost_usd, insights FROM matches WHERE id = :mid"),
                {"mid": match_id},
            ).mappings().all()
        else:
            rows = session.execute(
                text("SELECT id, created_at, game_id, outcome, parse_confidence, "
                     "cost_usd, insights FROM matches ORDER BY created_at DESC LIMIT 20")
            ).mappings().all()
    for r in rows:
        reps = [x for x in (r["insights"] or []) if x.get("kind") == "coaching_report"]
        if reps:
            return r, reps[0]
    return None, None


def build(row, rep) -> Path:
    from fpdf import FPDF

    p = rep.get("payload", {})
    out = row["outcome"] or {}
    side = p.get("player_side", "?")
    # Player-first, matching the app and the report body. This used to print the
    # raw home-away string while the body below it read the other way round.
    score = player_scoreline(out, side) or out.get("score", "?")
    result = str(out.get("result", "")).upper()
    corrected = out.get("score_source") == "vision_corrected"

    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_page()
    W = pdf.w - pdf.l_margin - pdf.r_margin

    # --- Header band ---
    pdf.set_fill_color(10, 102, 68)      # coach.io green
    pdf.rect(0, 0, pdf.w, 26, "F")
    pdf.set_xy(pdf.l_margin, 7)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("helvetica", "B", 18)
    pdf.cell(0, 8, _latin1("Coach.io  -  Match Coaching Report"))
    pdf.set_xy(pdf.l_margin, 16)
    pdf.set_font("helvetica", "", 10)
    created = row["created_at"]
    when = created.strftime("%Y-%m-%d %H:%M") if isinstance(created, datetime) else str(created)
    pdf.cell(0, 6, _latin1(f"{row['game_id']}   -   generated {when}"))
    pdf.ln(20)

    # --- Meta strip ---
    pdf.set_text_color(30, 30, 30)
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, _latin1(f"Final score {score}   ({result} for the {side} player)"), ln=1)
    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(90, 90, 90)
    meta = (f"Frames reviewed: {p.get('frames_reviewed', '?')}   |   "
            f"Segments read: {p.get('segments_read', '?')}   |   "
            f"Model: {rep.get('model', '?')}   |   "
            f"Cost: ${float(rep.get('cost_usd', 0) or 0):.4f}   |   "
            f"Parse confidence: {row.get('parse_confidence', '?')}")
    pdf.cell(0, 5, _latin1(meta), ln=1)
    if corrected:
        pdf.set_text_color(150, 60, 0)
        pdf.cell(0, 5, _latin1(f"Score corrected from OCR {out.get('score_ocr','?')} using the model's on-screen reads."), ln=1)
    pdf.ln(2)
    pdf.set_draw_color(210, 210, 210)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + W, pdf.get_y())
    pdf.ln(4)

    # --- Summary ---
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("helvetica", "B", 13)
    pdf.cell(0, 7, "Summary", ln=1)
    pdf.set_font("helvetica", "", 11)
    pdf.multi_cell(W, 6, _latin1(rep.get("summary", "")))
    pdf.ln(3)

    sections = [
        ("What you did well", "strengths", (10, 120, 70)),
        ("Recurring mistakes", "recurring_mistakes", (176, 32, 32)),
        ("Positioning issues", "positioning_issues", (176, 96, 0)),
        ("Decision-making patterns", "decision_patterns", (32, 80, 160)),
        ("What to practice", "practice_drills", (60, 60, 160)),
    ]
    for title, key, color in sections:
        items = p.get(key) or []
        if not items:
            continue
        if pdf.get_y() > pdf.h - 40:
            pdf.add_page()
        pdf.set_text_color(*color)
        pdf.set_font("helvetica", "B", 13)
        pdf.cell(0, 8, _latin1(title), ln=1)
        pdf.set_text_color(25, 25, 25)
        pdf.set_font("helvetica", "", 11)
        for it in items:
            y0 = pdf.get_y()
            pdf.set_x(pdf.l_margin + 2)
            pdf.cell(4, 6, _latin1("-"))
            pdf.set_x(pdf.l_margin + 6)
            pdf.multi_cell(W - 6, 6, _latin1(it))
            if pdf.get_y() == y0:
                pdf.ln(6)
        pdf.ln(2)

    # --- Stats strip (NEW) ---
    stats = p.get("stats") or {}
    if stats:
        _STAT_LABELS = [
            ("goals_for", "GF"), ("goals_against", "GA"), ("shots", "Shots"),
            ("big_chances", "Big chances"), ("goals_conceded_from_crosses", "Conceded (crosses)"),
            ("defensive_errors", "Def. errors"),
        ]
        parts = [f"{lbl}: {stats[k]}" for k, lbl in _STAT_LABELS if isinstance(stats.get(k), (int, float))]
        if parts:
            if pdf.get_y() > pdf.h - 30:
                pdf.add_page()
            pdf.set_text_color(20, 20, 20)
            pdf.set_font("helvetica", "B", 13)
            pdf.cell(0, 8, _latin1("Match stats"), ln=1)
            pdf.set_font("helvetica", "", 11)
            pdf.set_text_color(60, 60, 60)
            pdf.multi_cell(W, 6, _latin1("   |   ".join(parts)))
            pdf.ln(2)

    # --- Goal-by-goal (NEW) ---
    goals = p.get("goals") or []
    if goals:
        if pdf.get_y() > pdf.h - 40:
            pdf.add_page()
        pdf.set_text_color(20, 20, 20)
        pdf.set_font("helvetica", "B", 13)
        pdf.cell(0, 8, _latin1("Goal by goal"), ln=1)
        pdf.set_font("helvetica", "", 11)
        for g in goals:
            if pdf.get_y() > pdf.h - 24:
                pdf.add_page()
            scored = str(g.get("type", "")).lower().startswith("scor")
            pdf.set_text_color(*(10, 120, 70) if scored else (176, 32, 32))
            pdf.set_font("helvetica", "B", 11)
            tag = "GOAL" if scored else "CONCEDED"
            pdf.multi_cell(W, 6, _latin1(f"{g.get('time','')}  {tag} - {g.get('summary','')}"))
            deep = g.get("deep") or {}
            if deep.get("root_cause"):
                lbl = "Deep read" + (f" - {deep['defender']}" if deep.get("defender") else "")
                pdf.set_text_color(120, 60, 10)
                pdf.set_font("helvetica", "B", 10)
                pdf.set_x(pdf.l_margin + 6)
                pdf.multi_cell(W - 6, 5, _latin1(lbl))
                pdf.set_text_color(25, 25, 25)
                pdf.set_font("helvetica", "", 10)
                for line in (deep.get("what_happened"), deep.get("root_cause") and f"Root cause: {deep['root_cause']}", deep.get("fix") and f"Fix: {deep['fix']}"):
                    if line:
                        pdf.set_x(pdf.l_margin + 6)
                        pdf.multi_cell(W - 6, 5, _latin1(line))
            else:
                fix = g.get("fix")
                if fix and not scored:
                    pdf.set_text_color(25, 25, 25)
                    pdf.set_font("helvetica", "I", 10)
                    pdf.set_x(pdf.l_margin + 6)
                    pdf.multi_cell(W - 6, 5, _latin1(f"Fix: {fix}"))
            pdf.ln(1)

    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / (build.out_name or f"coaching_report_{row['id'][:8]}.pdf")
    pdf.output(str(path))
    return path


build.out_name = None  # type: ignore[attr-defined]


def main() -> None:
    # Usage: export_pdf [match_id|latest] [out_name]
    match_id = None
    if len(sys.argv) > 1 and sys.argv[1] not in ("latest", "-"):
        match_id = sys.argv[1]
    if len(sys.argv) > 2:
        name = sys.argv[2]
        build.out_name = name if name.endswith(".pdf") else name + ".pdf"  # type: ignore[attr-defined]

    row, rep = _report_for(match_id)
    if not row:
        print(f"No coaching_report found for {match_id or 'the last 20 matches'}.")
        sys.exit(1)
    path = build(row, rep)
    print(f"WROTE {path}  ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
