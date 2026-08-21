"""Build the short project description PDF: what it does, how, on what APIs,
and how it learns.

Hard capped at five pages. The cap is enforced by MEASUREMENT, not by estimating
how much prose fits: the document is rendered, its real page count read back, and
if it is over, one tier of optional content is dropped and it is rendered again.
Same approach as the in-app report PDF, for the same reason - a page budget that
depends on someone eyeballing paragraph lengths silently breaks the first time
the content changes.

No separate cover page. At five pages a full cover costs a fifth of the budget,
so the title sits as a block at the top of page one instead.

Run:  docker compose run --rm api python -m tools.build_overview_doc
Out:  docs/Coachfio-Project-Description.pdf
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

from tools._docstyle import (
    BODY, FULL, MARGIN, MUTED, PAGE_H, PAGE_W, RULE, S,
    P, bullets, callout, code, rule, table,
)

MAX_PAGES = 5
TITLE = "Coachfio"
SUBTITLE = "Project Description"
VERSION = "Revision 1.0"


def title_block(dated: str):
    """Masthead in place of a cover page."""
    t = Table([[Paragraph(TITLE, ParagraphStyle(
        "mt", parent=S["cover_t"], fontSize=27, leading=31, alignment=0))]],
        colWidths=[FULL], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    yield t
    yield Paragraph(
        "A game-agnostic gameplay analysis engine",
        ParagraphStyle("ms", parent=S["body"], fontSize=11.6, leading=15,
                       textColor=BODY, alignment=0, spaceAfter=2))
    yield Paragraph(
        f"{SUBTITLE}  |  {VERSION}  |  {dated}",
        ParagraphStyle("md", parent=S["cap"], alignment=0, spaceAfter=6))
    yield rule()
    yield Spacer(1, 9)


def story(tier: int = 0):
    """Document content. `tier` drops optional blocks in increasing order of
    importance so the five page cap can be met without mangling the argument."""
    dated = date.today().strftime("%d %B %Y")
    yield from title_block(dated)

    # ---- what it does ----
    yield P("What it does", "h1")
    yield P(
        "Coachfio turns a recording of a match you actually played into a specific, evidence "
        "backed coaching report. You upload footage. It returns a structured document naming "
        "the moment, the decision you made, the consequence, and what to do instead, with "
        "timestamps that jump straight back to the clip.", "lead")
    yield P(
        "The distinction that matters is this. A result screen tells you that you lost. It "
        "does not tell you why, and the two are not the same thing: a player can lose a match "
        "they played well and win one they played badly, so the outcome is a weak teaching "
        "signal. Feedback only changes behaviour when it is <b>specific</b>, tied to a moment "
        "that can be rewatched, <b>causal</b>, naming the decision rather than the result, and "
        "<b>actionable</b>, stating the alternative in terms the player can execute. Generic "
        "advice has none of the three.")
    if tier < 3:
        yield from callout(
            "It is an engine, not an app for one game.",
            "The core understands four abstractions only: Match, Event, Metric and Insight. "
            "Everything a particular game means lives in a plugin, and an automated test fails "
            "the build if a game identifier ever appears in the core. Two deliberately "
            "different titles run through it today: a football game read from video, and a "
            "tactical shooter read from a structured replay export with no video and no "
            "optical character recognition at all.")

    # ---- how it works ----
    yield P("How it works", "h1")
    yield P(
        "Upload is instant and analysis runs asynchronously on a worker, with progress "
        "streamed live to the browser. The pipeline is four steps.")
    yield bullets([
        "<b>Compress locally.</b> The recording is downscaled to 720p at 15 frames per "
        "second with audio stripped. This cuts upload time sharply and costs nothing in "
        "quality, because the model samples at roughly one frame per second anyway.",
        "<b>Two reads run in parallel.</b> The <b>coaching read</b> sends the whole recording "
        "to a video capable model, which watches it and fills a fixed nine section report "
        "schema. The <b>scoreboard read</b> samples frames every fifteen seconds, crops the "
        "scoreboard, and sends the crops in batches across concurrent requests. They need "
        "nothing from each other, so they overlap and the scoreboard read costs almost no "
        "wall clock time.",
        "<b>Reconcile.</b> The scoreboard read is the single authority on the result and "
        "restates the report body, so the match title and the document cannot state "
        "different scores.",
        "<b>Persist.</b> Outcome, metrics, events and the report are stored together with a "
        "per match breakdown of exactly where the money went.",
    ])
    yield P(
        "The scoreboard read deserves a sentence on why it exists. A score never decreases "
        "during a match, so every confirmed increment is exactly one goal. Watching the "
        "number change recovers the exact final score and the timing and side of every goal "
        "deterministically, instead of asking a model to remember a fifteen minute video "
        "correctly. Models are good at describing what a moment meant and unreliable at "
        "counting events across a long recording, so each job goes to the mechanism suited "
        "to it.")
    if tier < 2:
        yield P(
            "Stages run in cost order, cheapest first, and expensive models only ever see "
            "moments that a free local pass already flagged as worth looking at. A hard "
            "ceiling of 0.25 USD per fifteen minutes is enforced in code: a call that would "
            "breach it is refused before it runs rather than discovered afterwards.")
    yield from table([
        ["Measured on a real fifteen minute match", ""],
        ["End to end analysis time", "About 120 seconds"],
        ["Model spend per match", "About 0.06 USD"],
        ["Hard ceiling per fifteen minutes", "0.25 USD, enforced in code"],
        ["Score and goal timeline", "Recovered deterministically, not inferred"],
    ], [FULL - 52 * mm, 52 * mm])

    # ---- the output ----
    yield P("What the report contains", "h1")
    yield P(
        "The report is a fixed structure rather than free prose. A fixed structure is what "
        "makes reports comparable between matches and over time, and it stops the model "
        "writing at length about whatever it found easiest to describe.")
    yield from table([
        ["Section", "Contains"],
        ["Match context", "Mode, formations, result, score by phase, technical issues, what "
                          "the footage does and does not show, and a stated confidence"],
        ["Executive diagnosis", "Biggest strength, biggest repeatable mistake, the single "
                                "highest value habit to change, the main tactical and "
                                "mechanical problems"],
        ["Event log", "Per moment: time, phase, ball location, the player selected, the best "
                      "option available, what was actually done, why, the correction, "
                      "severity, and how often it recurred"],
        ["Attacking and defending", "Structured read of both phases, from build up angles and "
                                    "use of width to player switching, pressing angles and "
                                    "recovery after losing the ball"],
        ["Elite comparison", "Habits already shown, habits still missing, and the smallest "
                             "next step toward the standard"],
        ["Practice plan", "Exactly three ranked drills, each with repetitions, a success "
                          "metric, the common mistake and a correction phrase"],
        ["Tactical recommendation", "Current setting, proposed change, the problem it solves, "
                                    "the new weakness it creates, and when to reverse it"],
        ["Next video test", "What to record next, which metrics to compare, and the minimum "
                            "sample size before drawing a conclusion"],
    ], [38 * mm, FULL - 38 * mm])
    if tier < 1:
        yield P(
            "Two rules shape the writing. Every field is a string, including counts, because "
            "a model sampling at one frame per second cannot truthfully count touches: typed "
            "as numbers it fills them with confident invention, whereas as text it can answer "
            "that a value is not measurable from the footage, and the instructions require it "
            "to prefer that over a guess. And a correction must name the outlet: who to pass "
            "to, by name or shirt number, and what the pass is for. Telling a player to use "
            "the width is not coachable; telling them to switch to a named winger and hold "
            "him level with the six yard box so the back line drops is.")

    # ---- coaching ----
    yield P("Working with a coach", "h1")
    yield P(
        "Analysis is one half of the product. The other is a marketplace connecting players "
        "with real coaches. Players browse coach profiles carrying biography, specialities, "
        "experience, price and an explicit statement of what that price includes, then "
        "request the coach they want.")
    yield P(
        "Consent is structural rather than a scattering of permission checks. A link is "
        "always created by the player and there is no path by which a coach grants themselves "
        "access. A new link is pending and grants nothing at all, no chat and no reports, "
        "until the coach accepts. Report sharing is controlled separately, so an accepted "
        "link is necessary but not sufficient and the player still decides which reports are "
        "visible. Once accepted, both sides share a workspace with chat, an improvement "
        "checklist and the weaknesses that recur across the shared reports, which is what "
        "shows a coach the pattern without making them read every report to find it.")

    # ---- APIs ----
    yield P("What APIs it uses", "h1")
    yield P(
        "<b>Google Gemini</b> is the primary engine, called over plain HTTP with no vendor "
        "SDK dependency. The recording is uploaded once through the resumable File API and "
        "reused across every subsequent call, so additional viewings cost bandwidth rather "
        "than another upload.")
    yield code(
        "https://generativelanguage.googleapis.com\n"
        "  POST /upload/v1beta/files                resumable upload, returns upload URL\n"
        "  POST <upload-url>                        upload and finalize\n"
        "  GET  /v1beta/files/{name}                poll until ACTIVE, required before use\n"
        "  POST /v1beta/models/{model}:generateContent\n"
        "\n"
        "  gemini-flash-latest   watch, observe, read the scoreboard\n"
        "  gemini-pro-latest     deep synthesis in multi pass mode")
    yield P(
        "<b>The vision layer is pluggable</b>, selected by a single configuration value, so "
        "no provider is load bearing and the system can run entirely offline.")
    yield from table([
        ["Engine", "Use"],
        ["anthropic", "Claude Haiku for cheap frame classification, Sonnet for deep reads"],
        ["openai", "Any OpenAI compatible endpoint: OpenRouter, Moonshot, a local server"],
        ["ollama", "Fully local at zero cost, proven end to end on a single laptop"],
        ["stub", "Scripted offline model, so the test suite needs no key and no network"],
    ], [26 * mm, FULL - 26 * mm])
    yield P(
        "<b>Local, with no API involved:</b> ffmpeg for frame extraction, compression and "
        "clip assembly, and PaddleOCR with OpenCV for reading interface regions directly off "
        "the screen in the optical path.")
    yield P(
        "<b>Its own surface</b> is a REST API served by FastAPI, with Celery running analysis "
        "off the request path, PostgreSQL for structured data, Redis as both task broker and "
        "live progress bus, and S3 compatible object storage for recordings, frames and "
        "generated clips.")

    # ---- learning ----
    yield P("How it learns", "h1")
    yield from callout(
        "No model weights are trained and nothing is fine tuned.",
        "Learning here means accumulating grounded, sourced knowledge and longitudinal "
        "context, then retrieving only the relevant slice of it at analysis time. That "
        "distinction is worth stating plainly, because the alternative reading sets a "
        "false expectation about what the system is doing.")
    yield P(
        "There are four loops, and they compound: the first two grow the knowledge, the "
        "third makes it usable, and the fourth makes the coach aware of the individual "
        "player rather than treating every match as the first one it has seen.")
    yield bullets([
        "<b>It notices what it does not know.</b> Every analysis asks the model to list up to "
        "five game specific things it observed but could not confidently explain. Those gaps "
        "are deduplicated and queued.",
        "<b>It researches them.</b> A capped number of queued gaps per run are researched "
        "with search grounding, and answers that come back substantive are filed into the "
        "knowledge base together with their sources. Each research call is charged against "
        "the same budget as everything else, and the loop stops the moment the cap is hit, so "
        "learning cannot quietly become the largest line item.",
        "<b>It can be taught directly.</b> Knowledge is added by having the model watch an "
        "instructional video, by distilling written notes, or by writing structured entries "
        "by hand. Facts live as tagged files inside the relevant game's plugin, never in the "
        "core, split into mechanics, tactics, formations, player profiles, mistake remedies, "
        "patch notes and an analysis framework.",
        "<b>It retrieves selectively.</b> This is the loop that makes the other three useful. "
        "Injecting the whole knowledge base crowds out the actual observations and produces "
        "generic reports, so entries are ranked per match by relevance: explicit match terms "
        "weigh most, tags next, then weighted word overlap in which rare, discriminating "
        "words dominate and common ones contribute almost nothing. Only entries that clear "
        "the bar are injected.",
    ])
    yield P(
        "Underneath those, the coach carries memory of the player: how many matches they have "
        "analysed, their usual squad and formation, and their recurring weaknesses drawn from "
        "a controlled vocabulary so they aggregate cleanly across matches. That history goes "
        "into the prompt, which is what lets the report say a mistake is recurring rather "
        "than describing it as though for the first time.")
    if tier < 1:
        yield P(
            "One structural rule governs how any of this is applied. An error is classified "
            "before anything is prescribed, because the same visible outcome, a goal "
            "conceded, has several different causes and therefore several different correct "
            "fixes: a decision error, an execution error, a tactical one, a mechanical one, "
            "or simple external interference. Without that step the coach confidently "
            "recommends a formation change when the real problem was switching to the wrong "
            "defender a second too late.")

    if tier < 1:
        yield rule()
        yield Spacer(1, 4)
        yield P(f"{TITLE}. {SUBTITLE}. {VERSION}, {dated}.", "cap")


def build(path: Path) -> tuple[Path, int]:
    """Render, measure, and drop a tier of optional content until it fits."""
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

    pages = 0
    for tier in range(4):
        doc = BaseDocTemplate(
            str(path), pagesize=A4,
            leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
            title=f"{TITLE} {SUBTITLE}", author=TITLE,
            subject="A game-agnostic gameplay analysis engine")
        doc.addPageTemplates([PageTemplate(
            id="body",
            frames=[Frame(MARGIN, 19 * mm, FULL, PAGE_H - 30 * mm, id="body")],
            onPage=_page)])
        doc.build(list(story(tier)))
        pages = doc.page
        if pages <= MAX_PAGES:
            return path, pages
    # Every tier dropped and it still does not fit. Returning the oversized file
    # would quietly break the one guarantee this function makes, so say so instead.
    raise RuntimeError(
        f"cannot fit {path.name} into {MAX_PAGES} pages: {pages} even with all optional "
        f"content dropped. Cut mandatory content or raise MAX_PAGES.")


if __name__ == "__main__":
    out, n = build(Path("docs/Coachfio-Project-Description.pdf"))
    print(f"wrote {out} ({n} pages, {out.stat().st_size / 1024:.0f} KB)")
