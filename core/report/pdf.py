"""Coaching report -> PDF. Game-agnostic, like everything else in /core.

The envelope it reads (summary, strengths, recurring_mistakes, goals, stats...)
is the CORE coaching-report schema. The per-game sections and their headings are
NOT: they come from the match's adapter via `report_spec().kv_sections`. This
file used to carry its own copy of those forty football field labels while its
docstring claimed to be game-agnostic; a field added to the schema was then
answered by the model, stored by the API, shown on the web, and silently dropped
here. No game id appears in this file and none may be added - the adapter is
looked up from the match - see
`tests/test_core.py::test_no_game_branching_in_core`.

One builder, so that the emailed copy (planned) and the downloaded one can never
drift apart. Any second caller uses this, it does not get its own layout.

reportlab is used rather than an HTML-to-PDF engine because it is pure Python.
WeasyPrint would let us reuse the app's stylesheet but drags cairo/pango/harfbuzz
into the image, and the report is structured text - there is nothing to gain.

The palette here is deliberately NOT the app's dark theme: this document gets
printed and mailed, so it is dark ink on white.
"""
from __future__ import annotations

import re
from core.models.domain import player_scoreline
from datetime import datetime
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# The app's own tokens, so a downloaded report looks like the product it came
# from rather than a generic document.
PAGE = colors.HexColor("#0C1118")     # bg-primary
CARD = colors.HexColor("#151A21")     # bg-card - behind the label/value tables
INK = colors.HexColor("#F1F3F5")      # text-primary
SECOND = colors.HexColor("#C4CBD3")   # text-secondary
MUTED = colors.HexColor("#9AA3AD")    # text-muted
FAINT = colors.HexColor("#747E89")    # text-subtle
RULE = colors.HexColor("#293039")     # border
GOOD = colors.HexColor("#48C674")     # green
BAD = colors.HexColor("#D85C6B")      # danger
WARN = colors.HexColor("#D3A25E")
INFO = colors.HexColor("#7FA9D8")

# Section order and colour. Keys are core report-schema fields; the label is what
# the reader sees. Kept as data so adding a section is a one-line change.
_SECTIONS: list[tuple[str, str, colors.Color]] = [
    ("strengths", "What you did well", GOOD),
    ("recurring_mistakes", "Recurring mistakes", BAD),
    ("positioning_issues", "Positioning", WARN),
    ("decision_patterns", "Decision-making", INFO),
    ("practice_drills", "Practice focus", MUTED),
]

def _kv_sections(match: Any) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """The flat sections and their labels, from the match's own adapter.

    An unknown or unregistered game yields nothing rather than raising: the rest
    of the report - summary, strengths, mistakes, goals, practice plan - is core
    and still renders. A PDF missing its tactical sections beats no PDF.
    """
    from adapters.base.registry import UnknownGameError, load_builtin_adapters, registry

    # Idempotent, and cheap. Not relying on a caller having done it: the whole
    # point of this change is that a section must never go missing quietly, and
    # "the registry happened to be empty" is exactly that failure again.
    load_builtin_adapters()
    try:
        adapter = registry.get(_field(match, "game_id"), _field(match, "game_edition"))
    except (UnknownGameError, KeyError, AttributeError):
        return []
    return list(adapter.report_spec().kv_sections)


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["BodyText"]
    def s(name: str, **kw: Any) -> ParagraphStyle:
        return ParagraphStyle(name, parent=base, alignment=TA_LEFT, **kw)

    # Sized to fit a full report inside the page cap. Loose leading was costing
    # roughly a third of the document's capacity, which meant real reports hit
    # the trim ladder and lost their goal-by-goal section for no good reason.
    return {
        "title": s("cx-title", fontName="Helvetica-Bold", fontSize=19, leading=22,
                   textColor=INK, spaceAfter=2),
        "sub": s("cx-sub", fontSize=9.5, leading=13, textColor=MUTED, spaceAfter=0),
        "h2": s("cx-h2", fontName="Helvetica-Bold", fontSize=11.5, leading=14,
                textColor=INK, spaceBefore=12, spaceAfter=4),
        "body": s("cx-body", fontSize=9.5, leading=13.2, textColor=SECOND),
        "muted": s("cx-muted", fontSize=8.5, leading=12, textColor=MUTED),
        "faint": s("cx-faint", fontSize=7.5, leading=10, textColor=FAINT),
        "item": s("cx-item", fontSize=9.5, leading=13, textColor=SECOND, spaceAfter=2),
        "goal": s("cx-goal", fontSize=9.5, leading=13, textColor=SECOND),
        "fix": s("cx-fix", fontSize=9, leading=12.4, textColor=MUTED, leftIndent=10),
    }


def _esc(v: Any) -> str:
    """reportlab paragraphs accept a mini-HTML, so raw text must be escaped or a
    stray '&' or '<' in the coach's prose aborts the whole render."""
    return (
        str(v if v is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _points(value: Any) -> list[str]:
    """Report points are `{point, evidence_ids}` objects, but older rows stored
    plain strings. Accept both rather than lose a section to a schema change."""
    out: list[str] = []
    for item in value or []:
        if isinstance(item, dict):
            text = str(item.get("point") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _report_insight(match: Any) -> Any | None:
    for ins in getattr(match, "insights", None) or []:
        kind = ins.get("kind") if isinstance(ins, dict) else getattr(ins, "kind", "")
        if kind == "coaching_report":
            return ins
    return None


def _field(obj: Any, name: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def report_filename(match: Any) -> str:
    """Stable, filesystem- and header-safe name for the attachment/download."""
    outcome = getattr(match, "outcome", None) or {}
    side = str((getattr(match, "capture", None) or {}).get("player_side") or "home")
    line = player_scoreline(outcome, side) or str(outcome.get("score") or "")
    score = line.replace("-", "v") or "match"
    when = getattr(match, "created_at", None)
    day = when.strftime("%Y-%m-%d") if isinstance(when, datetime) else "report"
    return _SAFE_NAME.sub("-", f"coachfio-{day}-{score}.pdf")


def _kv_table(rows: list[tuple[str, str]], st: dict) -> Table:
    """Label/value block. A Table rather than paragraphs so long values wrap in
    their own column instead of running under the label."""
    data = [[Paragraph(_esc(label), st["muted"]), Paragraph(_esc(value), st["body"])]
            for label, value in rows]
    t = Table(data, colWidths=[44 * mm, 132 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
    ]))
    return t


def _clip(text: str, limit: int | None) -> str:
    """Cut on a word boundary so the last line is not half a word."""
    if not limit or len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "\u2026"


def _kv_rows(payload: dict, key: str, fields: list[tuple[str, str]],
             limit: int | None = None) -> list[tuple[str, str]]:
    block = payload.get(key)
    if not isinstance(block, dict):
        return []
    out = []
    for field, label in fields:
        value = str(block.get(field) or "").strip()
        if value:
            out.append((label, _clip(value, limit)))
    return out


# A report is meant to be read, not filed. Past five pages nobody finishes it.
MAX_PAGES = 5


def build_match_report_pdf(match: Any, *, player_name: str = "") -> bytes | None:
    """Render a match's coaching report, capped at MAX_PAGES.

    The cap is enforced by MEASURING, not by hoping the prompt keeps things
    short: render, count the pages reportlab actually produced, and if it is over
    budget re-render with less. The drop order is by how much each section is
    worth to a player reading it once:

        1  evidence log      - raw working; every line is restated above
        2  event log -> 6    - the tail repeats the same habit
        3  event log -> 3, and goal-by-goal, which the event log covers
        4  elite comparison, next-video test, match context
        5  clip remaining prose
        6  essentials only: summary, diagnosis, practice plan

    What survives to the last rung is the irreducible report: what went wrong,
    and what to do about it. Everything above that is evidence for those two, and
    a reader who never reaches page six was never going to read the evidence.

    In practice a real report renders at rung 0 or 1; the deeper rungs exist so
    the cap is a guarantee rather than a hope.

    Returns None when the match has no report - callers decide whether that is a
    404 or 'nothing to email'.
    """
    insight = _report_insight(match)
    if insight is None:
        return None

    for trim in range(7):
        pdf, pages = _render(match, insight, player_name, trim)
        if pages <= MAX_PAGES:
            return pdf
    return pdf   # trimmed as far as it goes; ship it rather than nothing


def _render(match: Any, insight: Any, player_name: str, trim: int) -> tuple[bytes, int]:

    payload: dict = _field(insight, "payload", {}) or {}
    kv_sections = _kv_sections(match)
    # Only the last resort clips prose; above that whole sections are dropped so
    # what survives is still complete.
    clip = 200 if trim >= 6 else (240 if trim >= 5 else None)
    essentials = trim >= 6
    summary = str(_field(insight, "summary", "") or "").strip()
    outcome: dict = getattr(match, "outcome", None) or {}
    st = _styles()

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=17 * mm, rightMargin=17 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title="Coachfio match report",
        author="Coachfio",
        subject="Coaching report",
    )

    flow: list[Any] = []
    flow.append(Paragraph("Coachfio match report", st["title"]))

    # Player-first, like the app, the web report and this document's own body.
    # This file was missed when the ordering was unified, so an away player got a
    # PDF headed 4-3 whose Match context read "3-4 Loss" two paragraphs down.
    side = str((getattr(match, "capture", None) or {}).get("player_side") or "home")
    score = (player_scoreline(outcome, side) or str(outcome.get("score") or "")).strip()
    result = str(outcome.get("result") or "").strip().title()
    when = getattr(match, "created_at", None)
    bits = [b for b in [
        f"{score} ({result})" if score and result else score or result,
        when.strftime("%d %b %Y") if isinstance(when, datetime) else "",
        f"for {player_name}" if player_name else "",
    ] if b]
    flow.append(Paragraph(_esc("  ·  ".join(bits)), st["sub"]))
    flow.append(Spacer(1, 6))
    flow.append(HRFlowable(width="100%", thickness=0.7, color=RULE, spaceAfter=9))

    if summary:
        flow.append(Paragraph("Coach's summary", st["h2"]))
        flow.append(Paragraph(_esc(summary), st["body"]))

    # Diagnosis and context lead the document - they are the answer, the rest is
    # the working.
    for key in (("diagnosis",) if trim >= 4 else ("diagnosis", "match_context")):
        section = next((x for x in kv_sections if x[0] == key), None)
        rows = _kv_rows(payload, key, section[2], clip) if section else []
        if rows:
            flow.append(Paragraph(section[1], st["h2"]))
            flow.append(_kv_table(rows, st))

    for key, label, colour in _SECTIONS:
        if essentials:
            break
        points = [_clip(x, clip) for x in _points(payload.get(key))]
        if not points:
            continue
        # keepWithNext, not KeepTogether: we only need the heading to stay with
        # its first bullet. KeepTogether treats heading+list as one indivisible
        # block, so a list that does not fit pushes the WHOLE thing to the next
        # page and leaves a third of this one empty.
        head = Paragraph(label, ParagraphStyle(
            "cx-h2-" + key, parent=st["h2"], textColor=colour, keepWithNext=1))
        # One Paragraph per point rather than a ListFlowable: a ListFlowable does
        # not split across pages, so a list that did not fit was moved WHOLE to
        # the next page and left a third of this one blank. Paragraphs flow.
        bullet = ParagraphStyle(
            "cx-bullet-" + key, parent=st["item"],
            leftIndent=12, bulletIndent=0,
            bulletFontSize=10, bulletColor=colour,
        )
        flow.append(head)
        for point in points:
            flow.append(Paragraph(_esc(point), bullet, bulletText="\u2022"))

    goals = [] if trim >= 3 else [g for g in (payload.get("goals") or []) if isinstance(g, dict)]
    if goals:
        flow.append(Paragraph("Goal by goal", st["h2"]))
        for g in goals:
            scored = str(g.get("type") or "").lower().startswith("scor")
            tag = "GOAL" if scored else "CONCEDED"
            colour = GOOD if scored else BAD
            line = (
                f'<font color="{colour.hexval()}"><b>{tag}</b></font>'
                f'  <font face="Courier">{_esc(g.get("time") or "")}</font>'
                f'  {_esc(g.get("summary") or "")}'
            )
            block: list[Any] = [Paragraph(line, st["goal"])]
            fix = str(g.get("fix") or "").strip()
            if fix and not scored:
                block.append(Paragraph("<b>Fix:</b> " + _esc(fix), st["fix"]))
            block.append(Spacer(1, 4))
            flow.append(KeepTogether(block))

    events = [] if essentials else [
        e for e in (payload.get("event_log") or []) if isinstance(e, dict)]
    if trim >= 2:
        events = events[:6]      # keep the earliest, most severe incidents
    if trim >= 3:
        events = events[:3]
    if events:
        flow.append(Paragraph("Event log", st["h2"]))
        for e in events:
            head_bits = [str(e.get("time") or ""), str(e.get("phase") or ""),
                         str(e.get("severity") or "").lower()]
            repeat = str(e.get("repeat_count") or "").strip()
            if repeat and repeat != "1":
                head_bits.append(f"x{repeat}")
            head = "  ".join(b for b in head_bits if b)
            rows = [(label, str(e.get(field) or "").strip()) for field, label in [
                ("ball_location", "Ball"), ("selected_player", "You controlled"),
                ("what_i_did", "What you did"), ("best_option", "Better option"),
                ("why", "Why"), ("correction", "Correction"),
            ] if str(e.get(field) or "").strip()]
            block: list[Any] = [Paragraph(f"<b>{_esc(head)}</b>", st["goal"])]
            if rows:
                block.append(_kv_table(rows, st))
            block.append(Spacer(1, 5))
            flow.append(KeepTogether(block))

    plan = [x for x in (payload.get("practice_plan") or []) if isinstance(x, dict)]
    if essentials:
        plan = plan[:3]
    if plan:
        flow.append(Paragraph("Practice plan", st["h2"]))
        for i, pr in enumerate(plan, 1):
            fields = [("drill", "Drill"), ("reps", "Repetitions"),
                      ("success_metric", "Success metric"),
                      ("common_mistake", "Common mistake"),
                      ("correction_phrase", "Say to yourself")]
            if essentials:
                fields = fields[:1]      # the drill is the actionable part
            rows = [(label, _clip(str(pr.get(field) or "").strip(), clip))
                    for field, label in fields if str(pr.get(field) or "").strip()]
            block = [Paragraph(
                f'<font color="{GOOD.hexval()}"><b>Priority {i}</b></font>  '
                + _esc(_clip(str(pr.get("problem") or ""), clip)), st["goal"])]
            if rows:
                block.append(_kv_table(rows, st))
            block.append(Spacer(1, 5))
            flow.append(KeepTogether(block))

    elite = None if trim >= 4 else payload.get("elite_comparison")
    if isinstance(elite, dict):
        parts: list[Any] = []
        for field, label in [("habits_already_shown", "Habits you already show"),
                             ("habits_missing", "Habits you lack")]:
            items = [str(x).strip() for x in (elite.get(field) or []) if str(x).strip()]
            if items:
                parts.append(Paragraph(f"<b>{label}</b>", st["body"]))
                parts.append(ListFlowable(
                    [ListItem(Paragraph(_esc(x), st["item"]), leftIndent=12) for x in items],
                    bulletType="bullet", start="•", bulletFontSize=10, bulletOffsetY=-1,
                    leftIndent=12, bulletColor=MUTED,
                ))
        rows = [(label, str(elite.get(field) or "").strip()) for field, label in [
            ("smallest_next_step", "Smallest thing to practise first"),
            ("reference_gaps", "Missing reference data"),
        ] if str(elite.get(field) or "").strip()]
        if rows:
            parts.append(_kv_table(rows, st))
        if parts:
            flow.append(Paragraph("Elite comparison", st["h2"]))
            flow.extend(parts)

    # An empty list is a real finding - the coach is told to change nothing unless
    # the video proves the tactic contributed. Print that rather than omit it.
    changes = None if essentials else payload.get("tactical_changes")
    if isinstance(changes, list):
        flow.append(Paragraph("Tactical recommendation", st["h2"]))
        if not changes:
            flow.append(Paragraph(
                "Nothing in this match pointed at your tactics. The problems above are "
                "habits, not settings.", st["body"]))
        for c in [x for x in changes if isinstance(x, dict)]:
            rows = [(label, str(c.get(field) or "").strip()) for field, label in [
                ("problem_it_solves", "Solves"),
                ("new_weakness_created", "New weakness this creates"),
                ("reverse_when", "Reverse it when"),
            ] if str(c.get(field) or "").strip()]
            block = [Paragraph(
                f'<b>{_esc(c.get("current_setting") or "")}</b> &rarr; '
                f'<b>{_esc(c.get("new_setting") or "")}</b>', st["goal"])]
            if rows:
                block.append(_kv_table(rows, st))
            block.append(Spacer(1, 5))
            flow.append(KeepTogether(block))

    for key, label, fields in kv_sections:
        if essentials:
            break
        if key in ("diagnosis", "match_context"):
            continue  # already rendered at the top
        if trim >= 4 and key == "next_video_test":
            continue
        rows = _kv_rows(payload, key, fields, clip)
        if rows:
            flow.append(Paragraph(label, st["h2"]))
            flow.append(_kv_table(rows, st))

    # The evidence log (the raw observation dump the analysis was built from) is
    # not printed at all: every line of it is restated in a section above, so it
    # only ever made the document longer. The web report drops it too - the two
    # must not disagree about what a report contains.
    evidence: list[str] = []
    if evidence:
        flow.append(PageBreak())
        flow.append(Paragraph("Evidence log", st["h2"]))
        flow.append(Paragraph(
            "Timestamped observations the points above were drawn from.", st["muted"]))
        flow.append(Spacer(1, 6))
        for line in evidence:
            flow.append(Paragraph(_esc(line), st["muted"]))

    def _page(canvas: Any, _doc: Any) -> None:
        """Paint the page, then the furniture. The fill has to come first and
        cover the whole sheet - reportlab renders onto white, so without it the
        light text would land on a white background and vanish."""
        canvas.saveState()
        canvas.setFillColor(PAGE)
        canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
        # Accent rule along the top edge - the one flash of green furniture.
        canvas.setFillColor(GOOD)
        canvas.rect(0, A4[1] - 3, A4[0], 3, stroke=0, fill=1)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(FAINT)
        canvas.drawString(17 * mm, 9 * mm, "Generated by Coachfio")
        canvas.drawRightString(A4[0] - 17 * mm, 9 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(flow, onFirstPage=_page, onLaterPages=_page)
    return buf.getvalue(), doc.page
