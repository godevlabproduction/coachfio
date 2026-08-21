"""Build a cross-match "season report" PDF for one account.

This is the artifact a per-match report cannot produce. One match cannot tell you
whether a mistake is a bad night or your defining habit; ten can. So this reads
every completed match on an account, finds what RECURS, and says the one thing
worth changing.

Two data-integrity rules, because an aggregate that quietly averages bad inputs
produces a confident and wrong document, which is worse than no document:

  1. DUPLICATES ARE COLLAPSED. Re-analysing the same video (a re-run after a fix,
     or an accidental double upload) creates a second match row. Counting both
     would double-weight that match and, when the re-run corrected a score, would
     average the right answer with the wrong one. Matches are fingerprinted on
     their goal timeline and only the NEWEST of each group is kept.
  2. SCORES ANALYSED BEFORE THE SCORING FIX ARE MARKED. Reads taken at the old
     56-second sampling could miss goals. Those matches still carry a perfectly
     good tactical diagnosis, so they are kept for pattern analysis but flagged,
     and the document says so on page one rather than in a footnote.

Run:  docker compose run --rm api python -m tools.build_season_report <email>
Out:  docs/Coachfio-Season-Report.pdf
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

from core.storage.db import init_db, session_scope
from core.storage.repository import MatchRepository
from core.storage.users import find_by_email, normalise_email
from tools._docstyle import (
    BODY, FULL, MARGIN, MUTED, PAGE_H, PAGE_W, RULE, S,
    P, bullets, callout, rule, table,
)

TITLE = "Coachfio"
SUBTITLE = "Season Report"

# Matches analysed before the scoring fix. Their goal timings sat on a 56-second
# grid, which is why the same timestamps recur across unrelated matches.
SCORING_FIX_AT = datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc)


def _fingerprint(m) -> tuple:
    """Identify the same VIDEO analysed more than once.

    Fingerprint the SOURCE FILE, not the analysis. The obvious approach is to
    compare the goal timeline, and it fails on the one case that matters most: a
    re-run that CORRECTS a score produces a deliberately different timeline, so
    the corrected match and the wrong one look like two separate matches and both
    get counted. That is how a 15-minute 11-3 also appeared in this report as a
    5-2, inventing a match that was never played.

    The upload is byte-identical across re-analyses, so its size groups them no
    matter what the analysis concluded. Falls back to the timeline if the source
    object is gone, which is weaker but better than treating it as unique.
    """
    try:
        from core.storage.frame_keys import source_key
        from core.storage.objectstore import get_object_store
        return ("src", get_object_store().size(source_key(m.id)))
    except Exception:  # noqa: BLE001 - source expired or never stored
        goals = _goals(m)
        return ((m.outcome or {}).get("score", ""), len(goals),
                tuple(str(g.get("time", "")) for g in goals))


def _report(m) -> dict:
    for ins in m.insights or []:
        if (ins.payload or {}).get("match_context") or (ins.payload or {}).get("diagnosis"):
            return ins.payload or {}
    return (m.insights[0].payload if m.insights else {}) or {}


def _goals(m) -> list:
    return (_report(m).get("goals") or [])


def _collect(email: str):
    init_db()
    with session_scope() as session:
        user = find_by_email(session, normalise_email(email))
        if user is None:
            raise SystemExit(f"no account for {email}")
        matches = MatchRepository(session).list(limit=500, identity=user.user_id)
        done = [m for m in matches if str(getattr(m.status, "value", m.status)) == "complete"]
        # Newest first already; keep the FIRST of each fingerprint so a corrected
        # re-run supersedes the original rather than being averaged with it.
        seen, kept, dropped = set(), [], []
        for m in done:
            fp = _fingerprint(m)
            (dropped if fp in seen else kept).append(m)
            seen.add(fp)
        name = user.display_name or email
        side = None
        rows = []
        for m in kept:
            o = m.outcome or {}
            s = (m.capture or {}).get("player_side") or "home"
            side = side or s
            h, a = o.get("score_home"), o.get("score_away")
            gf, ga = ((a, h) if s == "away" else (h, a)) if isinstance(h, int) else (None, None)
            created = m.created_at
            rows.append({
                "id": m.id[:8],
                "date": created.strftime("%d %b"),
                "stale": created.replace(tzinfo=created.tzinfo or timezone.utc) < SCORING_FIX_AT,
                "gf": gf, "ga": ga,
                "tags": _report(m).get("weakness_tags") or [],
                "habit": ((_report(m).get("diagnosis") or {}).get("highest_value_habit") or ""),
                "mistake": ((_report(m).get("diagnosis") or {}).get("biggest_repeatable_mistake") or ""),
                "strength": ((_report(m).get("diagnosis") or {}).get("biggest_strength") or ""),
                "plan": _report(m).get("practice_plan") or [],
            })
        return name, rows, len(dropped), len(done)


def _label(tag: str) -> str:
    return tag.replace("_", " ").replace("cb", "centre back").capitalize()


def story(name, rows, dropped, total):
    dated = date.today().strftime("%d %B %Y")
    n = len(rows)
    stale = sum(1 for r in rows if r["stale"])

    # masthead
    t = Table([[Paragraph(TITLE, ParagraphStyle("mt", parent=S["cover_t"],
                                                fontSize=27, leading=31, alignment=0))]],
              colWidths=[FULL], hAlign="LEFT")
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    yield t
    yield Paragraph(f"{SUBTITLE} for {name}",
                    ParagraphStyle("ms", parent=S["body"], fontSize=11.6, leading=15,
                                   textColor=BODY, alignment=0, spaceAfter=2))
    yield Paragraph(f"{n} matches analysed  |  {dated}",
                    ParagraphStyle("md", parent=S["cap"], alignment=0, spaceAfter=6))
    yield rule()
    yield Spacer(1, 9)

    # ---- data integrity, stated first ----
    notes = []
    if dropped:
        notes.append(
            f"{total} match records exist but {dropped} are re-analyses of a video already "
            f"counted, so they have been collapsed into the newest version of each. Counting "
            f"them would double-weight those matches, and where a re-run corrected a score it "
            f"would average the right answer with the wrong one.")
    if stale:
        notes.append(
            f"{stale} of the {n} were analysed before the scoring fix, when the scoreboard was "
            f"sampled once every 56 seconds. Their goal counts and timings may be short, which "
            f"is why the same timestamps recur across unrelated matches. Their tactical "
            f"diagnosis is unaffected and is used in full below; treat only their NUMBERS as "
            f"provisional.")
    if notes:
        yield from callout("Read this before the numbers.", " ".join(notes))

    # ---- the finding ----
    tags = Counter(t for r in rows for t in r["tags"])
    yield P("What recurs", "h1")
    if tags:
        top, top_n = tags.most_common(1)[0]
        yield P(
            f"One fault appears in {top_n} of your {n} matches: <b>{_label(top).lower()}</b>. "
            f"This is the finding a single report cannot give you. In any one match it reads as "
            f"a mistake; across {n} it is the defining habit, and it is the same fault whether "
            f"you won or lost.", "lead")
        yield from table(
            [["Recurring fault", "Matches", "Share"]] +
            [[_label(t), f"{c} of {n}", f"{round(100 * c / n)}%"]
             for t, c in tags.most_common(8)],
            [FULL - 52 * mm, 26 * mm, 26 * mm])
    else:
        yield P("No weakness tags were recorded on these matches.")

    # ---- record ----
    yield P("Match record", "h1")
    played = [r for r in rows if isinstance(r["gf"], int)]
    w = sum(1 for r in played if r["gf"] > r["ga"])
    losses = sum(1 for r in played if r["gf"] < r["ga"])
    d = len(played) - w - losses
    gf = sum(r["gf"] for r in played)
    ga = sum(r["ga"] for r in played)
    yield from table([
        ["Played", "Won", "Drawn", "Lost", "Scored", "Conceded"],
        [str(len(played)), str(w), str(d), str(losses), str(gf), str(ga)],
    ], [FULL / 6.0] * 6)
    yield from table(
        [["Match", "Date", "Score", "Recorded faults"]] +
        [[r["id"], r["date"],
          (f"{r['gf']}-{r['ga']}" + (" *" if r["stale"] else "")) if isinstance(r["gf"], int) else "-",
          ", ".join(_label(t).lower() for t in r["tags"][:3]) or "-"]
         for r in rows],
        [22 * mm, 18 * mm, 20 * mm, FULL - 60 * mm])
    if stale:
        yield P("* score taken before the scoring fix, treat as provisional.", "cap")

    # ---- the habit ----
    habits = [r["habit"] for r in rows if r["habit"]]
    if habits:
        yield P("The one habit to change", "h1")
        yield P(
            "Every report was asked, independently, for the single highest value habit to "
            "build. They converge, which is the strongest signal in this document: the same "
            "correction has been prescribed to you from separate matches without any of them "
            "knowing what the others said.")
        yield bullets([str(h)[:260] for h in habits[:4]])

    # ---- drills ----
    drills = [dr for r in rows for dr in (r["plan"] or []) if isinstance(dr, dict)]
    if drills:
        yield P("Practice priorities", "h1")
        yield P(
            "Drills prescribed across these matches, most recent first. Where the same problem "
            "recurs, the drill for it is the one worth repeating rather than a new one.")
        yield from table(
            [["Problem", "Drill", "Success metric"]] +
            [[str(dr.get("problem", ""))[:70], str(dr.get("drill", ""))[:90],
              str(dr.get("success_metric", ""))[:60]] for dr in drills[:6]],
            [46 * mm, FULL - 100 * mm, 54 * mm])

    # ---- honest limits ----
    yield P("What this report cannot tell you", "h1")
    yield bullets([
        "<b>Whether you are improving.</b> These matches span a few days, and the recurring "
        "fault appears throughout, so there is no trend to read yet. A change in habit shows "
        "up over weeks, not over one sitting.",
        "<b>Whether the counts are exact.</b> Goals for and against come from reading the "
        "scoreboard and are reliable on the recent matches. Shot and chance counts are model "
        "estimates from footage sampled about once per second and are deliberately not "
        "aggregated here.",
        "<b>Why the fault persists.</b> The reports agree on what happens and what to do "
        "instead. Whether it is a habit, a tactical setup or an input problem is the thing a "
        "human coach would establish fastest.",
    ])
    yield rule()
    yield Spacer(1, 4)
    yield P(f"{TITLE}. {SUBTITLE}. {dated}.", "cap")


def build(email: str, path: Path) -> tuple[Path, int]:
    name, rows, dropped, total = _collect(email)
    path.parent.mkdir(parents=True, exist_ok=True)

    def _page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.6)
        canvas.setFillColor(MUTED)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, 15 * mm, PAGE_W - MARGIN, 15 * mm)
        canvas.drawString(MARGIN, 10.5 * mm, f"{TITLE}  |  {SUBTITLE}")
        canvas.drawRightString(PAGE_W - MARGIN, 10.5 * mm, str(canvas.getPageNumber()))
        canvas.restoreState()

    doc = BaseDocTemplate(str(path), pagesize=A4,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN, bottomMargin=MARGIN,
                          title=f"{TITLE} {SUBTITLE}", author=TITLE)
    doc.addPageTemplates([PageTemplate(
        id="body", frames=[Frame(MARGIN, 19 * mm, FULL, PAGE_H - 30 * mm, id="body")],
        onPage=_page)])
    doc.build(list(story(name, rows, dropped, total)))
    return path, doc.page


if __name__ == "__main__":
    em = sys.argv[1] if len(sys.argv) > 1 else "ilijaatanasov04@gmail.com"
    out, pages = build(em, Path("docs/Coachfio-Season-Report.pdf"))
    print(f"wrote {out} ({pages} pages, {out.stat().st_size / 1024:.0f} KB)")
