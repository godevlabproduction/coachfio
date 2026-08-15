"""Build the product/technical documentation PDF.

Deliberately NOT themed like the app. The in-app report PDF (core/report/pdf.py)
is dark because it is a product surface; this is a document, so it uses a plain
light document theme that prints, photocopies and reads like documentation.

Run:  docker compose run --rm api python -m tools.build_product_doc
Out:  docs/Coachfio-Technical-Documentation.pdf
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, ListFlowable, ListItem, NextPageTemplate,
    PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

# ---- palette: neutral document, one accent -------------------------------------
INK = colors.HexColor("#12181F")
BODY = colors.HexColor("#2B3440")
MUTED = colors.HexColor("#5C6773")
ACCENT = colors.HexColor("#1B4F72")
RULE = colors.HexColor("#D3DAE0")
BAND = colors.HexColor("#F2F5F7")
CODEBG = colors.HexColor("#F7F9FA")

PAGE_W, PAGE_H = A4
MARGIN = 22 * mm

TITLE = "Coachfio"
SUBTITLE = "Technical and Product Documentation"
VERSION = "Revision 1.0"

_ss = getSampleStyleSheet()


def _styles() -> dict:
    return {
        "h1": ParagraphStyle("h1", parent=_ss["Heading1"], fontName="Helvetica-Bold",
                             fontSize=19, leading=24, textColor=ACCENT,
                             spaceBefore=2, spaceAfter=10),
        "h2": ParagraphStyle("h2", parent=_ss["Heading2"], fontName="Helvetica-Bold",
                             fontSize=13, leading=17, textColor=INK,
                             spaceBefore=14, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=_ss["Heading3"], fontName="Helvetica-Bold",
                             fontSize=10.5, leading=14, textColor=BODY,
                             spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("body", parent=_ss["BodyText"], fontName="Helvetica",
                               fontSize=9.6, leading=14.6, textColor=BODY,
                               alignment=TA_JUSTIFY, spaceAfter=7),
        "bullet": ParagraphStyle("bullet", parent=_ss["BodyText"], fontName="Helvetica",
                                 fontSize=9.6, leading=14.2, textColor=BODY,
                                 spaceAfter=3),
        "code": ParagraphStyle("code", parent=_ss["BodyText"], fontName="Courier",
                               fontSize=8.2, leading=11.6, textColor=INK,
                               backColor=CODEBG, borderPadding=7,
                               leftIndent=2, rightIndent=2, spaceAfter=8),
        "note": ParagraphStyle("note", parent=_ss["BodyText"], fontName="Helvetica-Oblique",
                               fontSize=9.2, leading=13.6, textColor=MUTED,
                               leftIndent=9, spaceAfter=8),
        "cap": ParagraphStyle("cap", parent=_ss["BodyText"], fontName="Helvetica",
                              fontSize=8.2, leading=11, textColor=MUTED, spaceAfter=10),
        "cover_t": ParagraphStyle("cover_t", parent=_ss["Title"], fontName="Helvetica-Bold",
                                  fontSize=40, leading=46, textColor=ACCENT,
                                  alignment=TA_CENTER),
        "cover_s": ParagraphStyle("cover_s", parent=_ss["Title"], fontName="Helvetica",
                                  fontSize=14.5, leading=20, textColor=BODY,
                                  alignment=TA_CENTER),
        "cover_m": ParagraphStyle("cover_m", parent=_ss["Title"], fontName="Helvetica",
                                  fontSize=9.5, leading=14, textColor=MUTED,
                                  alignment=TA_CENTER),
        "toc": ParagraphStyle("toc", parent=_ss["BodyText"], fontName="Helvetica",
                              fontSize=10, leading=17, textColor=BODY, spaceAfter=0),
    }


S = _styles()


# ---- flowable helpers ----------------------------------------------------------
def P(t, s="body"):
    return Paragraph(t, S[s])


def bullets(items, numbered=False):
    return ListFlowable(
        [ListItem(Paragraph(i, S["bullet"]), leftIndent=14) for i in items],
        bulletType="1" if numbered else "bullet",
        bulletFontSize=8.5, bulletColor=ACCENT, leftIndent=14, spaceAfter=9,
    )


def code(text):
    esc = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
           .replace(" ", "&nbsp;").replace("\n", "<br/>"))
    return Paragraph(esc, S["code"])


def table(rows, widths, header=True, align_right=()):
    data = [[Paragraph(f"<b>{c}</b>" if header and r == 0 else c,
                       ParagraphStyle("t", parent=S["bullet"], fontSize=8.6, leading=12,
                                      spaceAfter=0,
                                      textColor=INK if header and r == 0 else BODY))
             for c in row] for r, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), BAND),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.9, ACCENT)]
    for c in align_right:
        style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return [t, Spacer(1, 10)]


def rule():
    t = Table([[""]], colWidths=[PAGE_W - 2 * MARGIN], rowHeights=[1])
    t.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.7, RULE)]))
    return t


# ---- page furniture ------------------------------------------------------------
def _cover_page(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(ACCENT)
    canvas.rect(0, PAGE_H - 13 * mm, PAGE_W, 13 * mm, stroke=0, fill=1)
    canvas.setFillColor(RULE)
    canvas.rect(0, 0, PAGE_W, 5 * mm, stroke=0, fill=1)
    canvas.restoreState()


def _body_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, PAGE_H - 13 * mm, f"{TITLE}  |  {SUBTITLE}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, PAGE_H - 15 * mm, PAGE_W - MARGIN, PAGE_H - 15 * mm)
    canvas.line(MARGIN, 15 * mm, PAGE_W - MARGIN, 15 * mm)
    canvas.drawString(MARGIN, 10.5 * mm, VERSION)
    canvas.drawRightString(PAGE_W - MARGIN, 10.5 * mm, str(canvas.getPageNumber()))
    canvas.restoreState()


def build(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(str(path), pagesize=A4,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN, bottomMargin=MARGIN,
                          title=f"{TITLE} {SUBTITLE}", author=TITLE,
                          subject="Product and technical documentation")
    cover_frame = Frame(MARGIN, MARGIN, PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN, id="cover")
    body_frame = Frame(MARGIN, 19 * mm, PAGE_W - 2 * MARGIN, PAGE_H - 40 * mm, id="body")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame], onPage=_cover_page),
        PageTemplate(id="body", frames=[body_frame], onPage=_body_page),
    ])
    doc.build(list(story()))
    return path


# ---- content -------------------------------------------------------------------
FULL = PAGE_W - 2 * MARGIN


def story():
    y = date.today().strftime("%d %B %Y")

    # ---------------- cover ----------------
    yield Spacer(1, 52 * mm)
    yield P(TITLE, "cover_t")
    yield Spacer(1, 5 * mm)
    yield P(SUBTITLE, "cover_s")
    yield Spacer(1, 3 * mm)
    yield P("A game-agnostic gameplay analysis engine", "cover_m")
    yield Spacer(1, 42 * mm)
    yield rule()
    yield Spacer(1, 5 * mm)
    yield P(f"{VERSION}<br/>{y}<br/>Confidential", "cover_m")
    yield NextPageTemplate("body")
    yield PageBreak()

    # ---------------- contents ----------------
    yield P("Contents", "h1")
    toc = [
        ("1", "Executive summary"),
        ("2", "The problem"),
        ("3", "Product overview"),
        ("4", "Architectural principle: the core knows no games"),
        ("5", "Domain model"),
        ("6", "The adapter contract"),
        ("7", "Ingestion and source types"),
        ("8", "The analysis pipeline"),
        ("9", "The knowledge layer"),
        ("10", "The coaching report"),
        ("11", "Measurement accuracy and robustness"),
        ("12", "Cost control"),
        ("13", "Coaching relationships and the consent model"),
        ("14", "Progress tracking"),
        ("15", "System architecture and deployment"),
        ("16", "API reference"),
        ("17", "Persistence and object storage"),
        ("18", "Security, privacy and identity"),
        ("19", "Quality assurance"),
        ("20", "Configuration reference"),
        ("21", "Operational runbook"),
        ("22", "Current status, limitations and roadmap"),
        ("A", "Glossary"),
    ]
    rows = [[n, t] for n, t in toc]
    t = Table(rows, colWidths=[14 * mm, FULL - 14 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("TEXTCOLOR", (0, 0), (0, -1), ACCENT),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.8),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, RULE),
    ]))
    yield t
    yield PageBreak()

    # ---------------- 1 ----------------
    yield P("1. Executive summary", "h1")
    yield P(
        "Coachfio is an analysis engine that turns a recording of a played match into a "
        "specific, evidence backed coaching report. A player uploads footage of a session "
        "they played. The system watches it, identifies what happened and when, diagnoses "
        "the recurring mistakes behind the result, and returns a structured document that "
        "names the moment, the decision, the consequence and the correction.")
    yield P(
        "The product is deliberately built as an engine rather than an application for one "
        "title. The analytical core understands four abstractions only: a Match, an Event, a "
        "Metric and an Insight. Everything a specific game means is confined to a plugin. "
        "Adding a game is a plugin, not a rewrite, and the architecture is enforced by an "
        "automated test that fails the build if any game identifier appears in the core.")
    yield P(
        "Two very different titles have been run through the same core to prove the "
        "abstraction holds. The first is a football title analysed from video, where the "
        "score and events must be recovered from pixels. The second is a tactical shooter "
        "analysed from a structured replay export, with no video and no optical character "
        "recognition at all. The only change required in the core was general: the pipeline "
        "dispatches on the type of source, never on the game.")
    yield P(
        "Alongside analysis, the platform supports a coaching marketplace. Players browse "
        "coach profiles, request a coach, and on acceptance gain a shared workspace with "
        "chat, a progress checklist and selectively shared reports. Consent is structural: "
        "a link is always created by the player, and every permission a coach holds is "
        "derived from an accepted link.")

    yield P("Key characteristics", "h2")
    yield from table([
        ["Property", "Value"],
        ["Analysis input", "Whole match video, replay export, or public API data"],
        ["Typical turnaround", "Approximately two minutes for a fifteen minute match"],
        ["Typical cost per match", "Approximately 0.06 USD in model spend"],
        ["Hard cost ceiling", "0.25 USD per fifteen minutes, scaled by duration, enforced in code"],
        ["Games supported today", "Two, via independent plugins"],
        ["Core lines of game specific code", "Zero, guaranteed by an automated test"],
    ], [46 * mm, FULL - 46 * mm])

    # ---------------- 2 ----------------
    yield P("2. The problem", "h1")
    yield P(
        "Competitive players improve slowly because the feedback available to them is poor. "
        "The result screen tells them they lost. It does not tell them why, and the two are "
        "not the same thing. A player can lose a match they played well and win one they "
        "played badly, so the outcome is a weak teaching signal.")
    yield P(
        "The alternatives all have structural limits. Watching your own replay requires you "
        "to already know what a mistake looks like, which is precisely the knowledge a "
        "developing player lacks. Public guides are generic: they describe situations rather "
        "than your situation. Human coaching is genuinely effective but expensive, slow to "
        "schedule and hard to find.")
    yield P(
        "Three properties are needed for feedback to change behaviour, and generic advice "
        "has none of them. It must be <b>specific</b>, tied to a moment the player can watch "
        "again. It must be <b>causal</b>, naming the decision rather than the outcome. And it "
        "must be <b>actionable</b>, stating what to do instead in terms the player can "
        "execute. Coachfio is built to produce feedback with all three properties, at a cost "
        "per match measured in cents.")

    # ---------------- 3 ----------------
    yield P("3. Product overview", "h1")
    yield P("The player journey", "h2")
    yield bullets([
        "<b>Account and role.</b> On sign up a user declares whether they are a player or a "
        "coach. The role determines the navigation, the available pages and the permissions.",
        "<b>Calibration.</b> Players answer a short optional survey. The answers suggest a "
        "coaching level, because self assessment is unreliable. The suggestion is never "
        "binding: the player always chooses. Level changes how a report is pitched, so the "
        "same footage produces different documents for a beginner and an expert.",
        "<b>Upload.</b> The player uploads a recording and declares which side they played. "
        "Progress is streamed live while the analysis runs.",
        "<b>Report.</b> A structured coaching report is produced, with timestamps that jump "
        "to the moment in the footage, and is downloadable as a PDF.",
        "<b>Progress.</b> Metrics from every match feed a longitudinal view, so recurring "
        "weaknesses are visible across matches rather than only within one.",
        "<b>Coaching.</b> The player may browse coach profiles and request one. On "
        "acceptance they gain chat, a shared improvement checklist and selective report "
        "sharing.",
    ])
    yield P("The coach journey", "h2")
    yield bullets([
        "<b>Public profile.</b> Biography, specialities, experience, price and an explicit "
        "statement of what the price includes.",
        "<b>Requests.</b> Incoming player requests are accepted or declined. Nothing is "
        "shared until acceptance.",
        "<b>Client workspace.</b> Per client view of shared reports, recurring weaknesses "
        "aggregated across matches, chat and the shared checklist.",
        "<b>Analysis on behalf of a player.</b> A coach may analyse footage of a named "
        "athlete. The report is then written in the third person, addressed to the coach "
        "about the player, rather than to the player directly.",
    ])

    # ---------------- 4 ----------------
    yield P("4. Architectural principle: the core knows no games", "h1")
    yield P(
        "One rule governs the codebase. The analytical core understands Match, Event, Metric "
        "and Insight. It never contains logic conditional on which game is being analysed. "
        "All game meaning lives in a plugin under the adapters directory.")
    yield P(
        "This is not a convention that depends on discipline. It is enforced by a test that "
        "scans the core for any registered game identifier and fails the build if one "
        "appears. The practical effect is that the urge to special case a game is caught "
        "immediately and treated as a design error to be pushed into an adapter.")
    yield P("What this buys", "h2")
    yield bullets([
        "<b>Adding a game does not modify the core.</b> A new title is a new directory: an "
        "identity file, a declarative configuration, and a small amount of interpretation "
        "code.",
        "<b>Every product feature is inherited for free.</b> Progress tracking, cost "
        "control, storage, billing, the coaching marketplace and the report pipeline are "
        "written once against the four abstractions and work for every game.",
        "<b>Annual editions are isolated.</b> Adapters are versioned per edition, not per "
        "franchise, so a new edition of a game cannot break the previous one.",
        "<b>The abstraction is falsifiable.</b> A second game with entirely different "
        "properties, no video and no optical character recognition, runs through the same "
        "core unchanged. This is evidence rather than assertion.",
    ])
    yield P(
        "Adapters are roughly ninety percent declarative. Screen regions, metric "
        "definitions, event vocabulary and identity are data files. Only interpretation, the "
        "step that turns raw readings into meaning, is code. Screen coordinates are stored "
        "normalised between zero and one, so a single definition scales across every "
        "resolution rather than requiring one per display mode.", "note")

    # ---------------- 5 ----------------
    yield P("5. Domain model", "h1")
    yield P(
        "Four types are the entire vocabulary shared between the core and every adapter. "
        "Adapters produce them; storage, progress tracking, reporting and billing consume "
        "them and never change when a game is added.")
    yield from table([
        ["Type", "Meaning", "Notable fields"],
        ["Match", "A bounded session with a start, an end and an outcome",
         "game identity, source type, status, capture context, outcome, cost, warnings"],
        ["Event", "A timestamped occurrence, normalised to a core category",
         "timestamp, category, original game word, confidence, supporting frames"],
        ["Metric", "A single number extracted from a match",
         "key, label, value, unit, direction of improvement, source, confidence"],
        ["Insight", "A pattern, usually spanning a whole match or several",
         "scope, kind, summary, structured payload, model, cost"],
    ], [24 * mm, 52 * mm, FULL - 76 * mm])

    yield P("Normalised event categories", "h2")
    yield P(
        "An adapter maps its own vocabulary onto a fixed set of core categories. The core "
        "never sees a game word. This is what allows one progress and reporting layer to "
        "serve a football title and a shooter simultaneously.")
    yield from table([
        ["Category", "Meaning across games"],
        ["Score change", "The scoreline moved: a goal, a point, a round won"],
        ["Period boundary", "A half, quarter or round started or ended"],
        ["Stat snapshot", "A statistics or summary screen was read"],
        ["Discipline", "A sanction: a card, a foul, a penalty"],
        ["Roster change", "A substitution or swap"],
        ["Scene change", "A replay, cutscene or menu boundary"],
        ["Highlight", "A moment flagged as important"],
    ], [40 * mm, FULL - 40 * mm])

    yield P("Direction of improvement", "h2")
    yield P(
        "Each metric declares whether a higher value is better. This single flag is what "
        "lets a completely game agnostic progress layer describe a change as an improvement "
        "or a regression without understanding what the metric means. Goals conceded and "
        "goals scored are treated identically by the code and correctly by the interface.")

    yield P("Provenance and confidence", "h2")
    yield P(
        "Every metric records where it came from: read locally from the screen, inferred by "
        "a model, or derived from other values. Every metric and event also carries a "
        "confidence. This matters because these are measurements of a noisy source, and the "
        "interface should be able to distinguish a number that was read from one that was "
        "estimated.")

    # ---------------- 6 ----------------
    yield P("6. The adapter contract", "h1")
    yield P(
        "A game plugin implements a single interface. Most methods have sensible defaults "
        "derived from the adapter's own declared vocabulary, so a minimal adapter is small.")
    yield from table([
        ["Method", "Responsibility", "Required"],
        ["identity", "Game id, edition, display name, platforms, supported sources", "Yes"],
        ["hud_schema", "Screen regions to read, optionally varied by capture context", "Yes"],
        ["event_vocabulary", "This game's event words, each mapped to a core category", "Yes"],
        ["metric_definitions", "Metrics this game produces and their direction", "Yes"],
        ["interpret", "Turn raw screen readings into outcome, metrics and events", "Yes"],
        ["ingest", "Parse a replay or API payload directly, for non video sources", "No"],
        ["validate", "Sanity checks, returning warnings rather than throwing", "No"],
        ["coaching_playbook", "Game knowledge used to ground the coaching model", "No"],
        ["issue_vocabulary", "Controlled weakness tags for longitudinal aggregation", "No"],
        ["skill_survey", "Questions that hint at player level", "No"],
        ["suggest_skill_level", "Map survey answers to a suggested level", "No"],
        ["name_badge_regions", "Where player names appear, so coaching can name people", "No"],
    ], [37 * mm, FULL - 57 * mm, 20 * mm])
    yield P(
        "The interpretation method is described in the source as the ten percent of an "
        "adapter that is genuinely code. Everything else is configuration. An adapter for a "
        "video game and an adapter for a replay based game implement disjoint halves of this "
        "interface: the first implements interpretation and leaves ingestion unimplemented, "
        "the second does the reverse.", "note")

    yield P("Adapter resolution", "h2")
    yield P(
        "A registry maps a game identifier and edition to an adapter instance. This lookup "
        "table is the only place in the system that knows which games exist, and it is a "
        "table rather than a branch.")
    yield code(
        "registry.get(game_id, edition)   ->  GameAdapter\n"
        "key format:  \"<game_id>@<edition>\"     for example  \"ea-fc@26\"\n"
        "\n"
        "Editions are independent adapters. Shipping edition 27 cannot\n"
        "regress edition 26, because they are different registered objects.")

    # ---------------- 7 ----------------
    yield P("7. Ingestion and source types", "h1")
    yield P(
        "The pipeline dispatches on how data arrived, never on which game it belongs to. "
        "Source type is a core concept, and this is what allowed a second game with no video "
        "at all to be added without a core rewrite.")
    yield from table([
        ["Source type", "Path", "Relative cost"],
        ["Native video", "Whole recording sent to a video capable model", "Low"],
        ["Video", "Frames extracted locally, then optical character recognition", "Local processing only"],
        ["Replay file", "Structured export parsed directly by the adapter", "Near zero"],
        ["Public API", "Structured data fetched from the game's own service", "Near zero"],
        ["Screenshot", "Single frame read", "Very low"],
    ], [30 * mm, FULL - 68 * mm, 38 * mm])
    yield P(
        "The current primary path is native video. A recording is compressed locally, "
        "uploaded once, and analysed by a video capable model that watches it directly. This "
        "replaced an earlier approach of extracting frames and reading them optically, which "
        "remains in the codebase and remains the path a game would use if no video capable "
        "model suited it.")

    # ---------------- 8 ----------------
    yield P("8. The analysis pipeline", "h1")
    yield P(
        "Analysis is a sequence of stages executed in cost order, cheapest first, each able "
        "to declare itself disabled. The orchestrator enforces the budget, records the final "
        "cost and status, and guarantees that a stage failure is recorded rather than "
        "silently swallowed.")
    yield from table([
        ["Stage", "Function", "Cost"],
        ["Local extraction", "Frame sampling, scene differencing, screen reading, or direct "
                             "parsing of a replay depending on source type", "Zero, fully local"],
        ["Cheap classification", "A small vision model labels only candidate frames, using "
                                 "an enumeration supplied by the adapter", "Low"],
        ["Deep read", "A larger model examines only the moments already identified as "
                      "important, and returns structured insight", "Moderate"],
        ["Native video coaching", "A video capable model watches the whole recording and "
                                  "produces the structured report, in parallel with a "
                                  "deterministic scoreboard read", "Primary path"],
        ["Highlight clips", "Short clips assembled locally around each important event",
         "Zero, fully local"],
    ], [36 * mm, FULL - 74 * mm, 38 * mm])

    yield P("Two modes of the primary path", "h2")
    yield bullets([
        "<b>Single call mode.</b> One coaching pass over the whole recording, plus a "
        "deterministic scoreboard read that recovers the exact score and the timing of every "
        "goal. This is the default and represents the best ratio of quality to cost.",
        "<b>Multi pass mode.</b> The recording is watched several times independently and "
        "only observations that recur across viewings are kept. Passes run in parallel, so "
        "several viewings cost roughly the wall time of one. This suppresses one off "
        "hallucinations at higher spend, and additionally performs a close re read of the "
        "seconds around each goal.",
    ])
    yield P(
        "Cross viewing agreement is the mechanism that makes multi pass mode more than "
        "repetition. An observation seen once is discarded as noise; an observation seen by "
        "two independent viewings is retained. This is a consensus filter, not a majority "
        "vote on a single answer.", "note")

    yield P("Parallelism", "h2")
    yield P(
        "The scoreboard read and the coaching read need nothing from each other. One works "
        "from extracted frames, the other from the uploaded recording. They originally ran "
        "sequentially, which meant the entire scoreboard phase was dead time added to the "
        "total. They now overlap, and the scoreboard phase contributes almost nothing to "
        "wall clock time. Measured on a real fifteen minute match, this and the associated "
        "batching reduced total analysis time from 295 seconds to approximately 120, with "
        "identical output.")

    # ---------------- 9 ----------------
    yield P("9. The knowledge layer", "h1")
    yield P(
        "A general model asked to coach produces generic advice: play better, use the width, "
        "defend properly. None of that changes behaviour. The knowledge layer exists to "
        "ground the coaching model in real, current, specific mechanics of the game being "
        "analysed, so that a correction names an actual input, an actual role and an actual "
        "consequence.")
    yield P(
        "Knowledge is stored per game as structured files inside that game's adapter, never "
        "in the core. Entries are short, factual and tagged. At analysis time a relevance "
        "ranked subset is selected and injected, rather than the whole body, because a large "
        "reference dump crowds out the observations that make a report specific.")
    yield from table([
        ["Knowledge category", "Purpose"],
        ["Mechanics", "Exact inputs and what they do, including mechanics removed in the "
                      "current version so the coach never recommends them"],
        ["Tactics and formations", "Structural options and the weakness each one creates"],
        ["Player profiles", "Which attributes actually matter for a role, as distinct from "
                            "an overall rating"],
        ["Mistake remedies", "Named errors mapped to concrete corrections"],
        ["Analysis framework", "The classification scheme the coach reasons with"],
        ["Patch notes", "Version specific changes, so advice does not silently go stale"],
        ["Learned", "Automatically researched facts, recorded with their source"],
    ], [46 * mm, FULL - 46 * mm])

    yield P("Classify before prescribing", "h2")
    yield P(
        "The highest value part of the knowledge layer is an error taxonomy, and the reason "
        "is worth stating precisely. The same visible outcome, a goal conceded, can have "
        "several different causes and therefore several different correct fixes. Without a "
        "taxonomy the coach recommends a formation change when the real problem was a late "
        "player switch. Naming the type of error first is what makes the fix land on the "
        "actual cause.")
    yield from table([
        ["Error type", "Definition", "Implication"],
        ["Decision", "The right option existed and was not chosen", "Coach the read"],
        ["Execution", "The idea was correct, the action failed", "Coach the technique"],
        ["Tactical", "The setup put the player in a position to fail", "Change the setup"],
        ["Mechanical", "An input problem rather than a thinking problem", "Drill the input"],
        ["External", "Latency, an animation, an unusual event", "Name it and move on"],
    ], [26 * mm, FULL - 74 * mm, 48 * mm])

    yield P("Self extension", "h2")
    yield P(
        "The model records questions it could not answer from the knowledge available. These "
        "gaps are queued, and a bounded number are researched per run and written back as "
        "sourced entries. Knowledge can also be added by distilling a written source or by "
        "having the model watch an instructional video. The number of research calls per run "
        "is capped so the system cannot spend without limit on learning.")

    # ---------------- 10 ----------------
    yield P("10. The coaching report", "h1")
    yield P(
        "The report is a fixed structure rather than free prose. A fixed structure is what "
        "makes reports comparable across matches and across time, and it prevents the model "
        "from writing at length about whatever it found easiest to describe.")
    yield from table([
        ["Section", "Contains"],
        ["Match context", "Mode, formations, result, score by phase, technical issues, what "
                          "the footage does and does not show, and a stated confidence"],
        ["Executive diagnosis", "Biggest strength, biggest repeatable mistake, the single "
                                "highest value habit to change, main tactical problem, main "
                                "mechanical problem"],
        ["Event log", "Per moment: time, phase, ball location, selected player, the best "
                      "option available, what was actually done, why it happened, the "
                      "correction, severity and how often it recurred"],
        ["Attacking analysis", "Build up angles, use of width, half space occupation, runs, "
                               "striker and attacking midfield movement, overlaps, cutback "
                               "creation, shot selection, rushed final actions"],
        ["Defensive analysis", "Shape, holding midfield positioning, centre back movement, "
                               "player switching, containment and sprint usage, pressing "
                               "angles, prevention of through balls and cutbacks, recovery, "
                               "fullback exposure"],
        ["Elite comparison", "Habits already demonstrated, habits still missing, and the "
                             "smallest next step toward the standard"],
        ["Practice plan", "Exactly three ranked drills, each with repetitions, a success "
                          "metric, the common mistake and a correction phrase"],
        ["Tactical recommendation", "Current setting, proposed setting, the problem it "
                                    "solves, the new weakness it creates, and when to "
                                    "reverse it"],
        ["Next video test", "What to record next and which metrics to compare, including a "
                            "minimum sample size"],
    ], [40 * mm, FULL - 40 * mm])

    yield P("Design decisions worth recording", "h2")
    yield bullets([
        "<b>Every field is a string, including counts.</b> The model samples the recording "
        "at roughly one frame per second, so it cannot truthfully count touches or classify "
        "every pass. Typed as numbers it would fill them with confident invention. As "
        "strings it can answer that a value is not measurable from the footage, and the "
        "instructions require it to prefer that over a guess. An honest gap is useful; a "
        "fabricated number is worse than nothing.",
        "<b>Tactical changes may legitimately be empty.</b> The instruction is to change "
        "nothing unless the footage proves the current setup contributed to the problem. A "
        "recommendation engine that always recommends something is noise.",
        "<b>Corrections must name the outlet.</b> When the fix is to pass elsewhere, the "
        "report must say to whom, by name or shirt number, and what the pass is for. Naming "
        "a specific player and the purpose of the movement is coachable; advising a player "
        "to use the width is not.",
        "<b>Every point carries a timestamp.</b> Points reference the position in the "
        "recording, not the in game clock, and any timestamp falling outside the recording "
        "is discarded so that jumping to a moment is always safe.",
        "<b>Sections must not repeat each other.</b> The instructions define what each "
        "section is for and forbid restating a point already made elsewhere.",
        "<b>Reports are pitched to the declared level.</b> The same footage produces a "
        "different document for a beginner and an expert, because advice written for one is "
        "unusable by the other.",
    ])

    yield P("Report delivery", "h2")
    yield P(
        "Reports are readable in the application with timestamps that jump to the moment, "
        "and downloadable as a PDF. The report PDF is capped at five pages: it is rendered, "
        "measured, and if it exceeds the cap, content is trimmed in a defined order of "
        "increasing importance and rendered again. The cap is enforced by measurement rather "
        "than estimation.")

    # ---------------- 11 ----------------
    yield P("11. Measurement accuracy and robustness", "h1")
    yield P(
        "Reading a scoreboard from footage sounds trivial and is not. Recordings contain "
        "replays, cutscenes, menus and celebrations, all of which can put confident but "
        "wrong values into the region where the score normally sits. Several layers of "
        "defence exist, and each one was added in response to a real failure.")
    yield P("Layered defences", "h2")
    yield bullets([
        "<b>Plausibility limits.</b> Values outside a credible range are rejected outright. "
        "A crest merging with a digit once produced a reading of ninety four at full "
        "confidence.",
        "<b>Live frame gating.</b> Only frames whose clock parses as a valid match time are "
        "trusted in the optical path.",
        "<b>Monotonic consistency.</b> A score never decreases during a match. Any reading "
        "that goes backwards is a replay, a stale overlay or a misread, and is discarded.",
        "<b>Consensus rather than maximum.</b> The final score is decided by agreement near "
        "the end of the match, never by taking the largest value seen. A transient phantom "
        "that reverts never survives to the end.",
        "<b>Derived events capped by the final score.</b> The number of scoring events "
        "cannot exceed the score they are supposed to explain.",
    ])

    yield P("A worked example of a real defect", "h2")
    yield P(
        "The value of documenting this lies in the failure mode rather than the fix. A "
        "reading was originally trusted only once it had been seen twice, on the reasoning "
        "that a phantom value appears briefly and then reverts. The reasoning was sound but "
        "the test was wrong. In a high scoring match the real score also changes between "
        "almost every pair of samples, so every genuine late value appeared exactly once and "
        "was discarded as unconfirmed. The true final score was then compared against a "
        "stale surviving value, judged an implausible jump, and rejected as well.")
    yield P(
        "A real match that finished eleven to three was reported as five to two. Critically, "
        "the error grew with the scoreline: the more eventful the match, the more wrong the "
        "output, which is the worst possible direction for a measurement error to fail in.")
    yield P(
        "The correction was to test the property that actually distinguishes the two cases. "
        "A phantom value goes backwards; a real score never does. A reading is now trusted "
        "when it does not decrease, when the increment is plausible, and when nothing later "
        "contradicts it. Sampling density was increased at the same time, which the batching "
        "work had made affordable, and a targeted second sweep was added over the end of "
        "play, because recordings usually continue past the final whistle into menus and a "
        "goal scored in stoppage time falls into that blind spot.")
    yield P(
        "Three general lessons are recorded here deliberately. Confirmation by repetition "
        "fails exactly when the underlying quantity is changing quickly. A measurement error "
        "that scales with the magnitude of what is being measured is far more dangerous than "
        "a constant one. And verifying that two runs agree with each other is not the same "
        "as verifying that either is correct: agreement only proves reproducibility.", "note")

    yield P("Consistency between derived documents", "h2")
    yield P(
        "The match title and the body of the report originally drew their result from two "
        "independent sources, the deterministic scoreboard read and the model's own reading "
        "while watching, with nothing comparing them. A document could therefore contradict "
        "itself. The scoreboard read is now the single authority and restates the body, so "
        "the two cannot disagree.")

    # ---------------- 12 ----------------
    yield P("12. Cost control", "h1")
    yield P(
        "Cost is treated as a correctness property rather than an operational concern. The "
        "budget is enforced in code, and the system fails loudly rather than quietly "
        "overspending.")
    yield bullets([
        "A per match ceiling is defined and scaled linearly with the duration of the match, "
        "so a long recording is allowed proportionally more and a short one is not.",
        "Every charge is recorded with a label, producing a per match breakdown of exactly "
        "where money went.",
        "A call that would breach the ceiling is refused <b>before</b> it is made, using an "
        "estimate, rather than being discovered afterwards.",
        "Breaching the budget moves the match into a distinct terminal state, so an over "
        "budget match is visibly different from a failed one and from a successful one.",
        "Locally run and stubbed model backends are marked free, because a zero cost path "
        "cannot meaningfully be halted by a spending cap.",
    ])
    yield P(
        "Stages are ordered by cost, cheapest first, and expensive stages are restricted to "
        "moments already identified as important by cheaper ones. This is the central "
        "economic idea of the pipeline: never send an expensive model a frame that a free "
        "local process could have discarded.")

    # ---------------- 13 ----------------
    yield P("13. Coaching relationships and the consent model", "h1")
    yield P(
        "The relationship between a player and a coach carries real data sensitivity, so "
        "consent is expressed structurally rather than as a series of permission checks "
        "scattered through the code.")
    yield bullets([
        "A link is <b>always</b> created by the player. There is no code path by which a "
        "coach grants themselves access to a player.",
        "A newly created link is pending and grants nothing at all: no chat, no summary, no "
        "reports. The coach must accept it.",
        "Declining deletes the link rather than leaving a rejected record, so a player may "
        "ask again later without being permanently blocked by a past decision.",
        "Every permission funnels through a single predicate that returns true only for an "
        "accepted link. Remove that property and the module becomes a data leak, which is "
        "why it is centralised in one function rather than repeated.",
        "Report sharing is separately controlled. An accepted link is necessary but not "
        "sufficient: the player still decides which reports are visible.",
        "Coach profiles are exposed through an explicit allow list of fields. Contact "
        "details, survey answers and usage records are never included by construction, "
        "rather than by remembering to exclude them.",
    ])
    yield P("Shared workspace", "h2")
    yield P(
        "Once a link is accepted, both parties see a shared workspace containing chat, an "
        "improvement checklist, the reports the player has chosen to share, and weaknesses "
        "aggregated across those shared reports. The aggregate view is what makes coaching "
        "efficient: it shows the coach what recurs rather than requiring them to read every "
        "report to find out.")

    # ---------------- 14 ----------------
    yield P("14. Progress tracking", "h1")
    yield P(
        "Progress is computed entirely from metrics and the direction of improvement each "
        "one declares. The progress layer contains no game knowledge whatsoever, which is "
        "why it serves every adapter without modification.")
    yield P(
        "For each metric the system tracks the series of values across matches, the most "
        "recent value, the previous value, the change between them, whether that change "
        "counts as improvement given the metric's declared direction, and the average over a "
        "selected window of recent matches.")
    yield P(
        "This area is documented honestly as the weakest part of the product. Two structural "
        "limitations are known and understood. First, the headline figure compares the most "
        "recent match with the one before it, and both of those fall inside every selectable "
        "window, so most of the interface does not respond to the window control. Second, "
        "the chart plots each value against the average of the window being displayed, which "
        "makes the baseline move with the data: a player improving steadily and a player who "
        "is flat render almost identically, because the yardstick recalibrates. A fixed "
        "baseline and a window against window comparison are the identified corrections.",
        "note")
    yield P(
        "Metric trustworthiness varies and should be surfaced. Values derived from a "
        "deterministic reading of the scoreboard are reliable. Values that require counting "
        "discrete actions across a recording sampled at roughly one frame per second are "
        "estimates, and presenting the two with equal visual weight overstates the second.")

    # ---------------- 15 ----------------
    yield P("15. System architecture and deployment", "h1")
    yield P(
        "The system runs as a small set of containers. Analysis is asynchronous: the "
        "interface never blocks on a model call.")
    yield from table([
        ["Component", "Technology", "Responsibility"],
        ["API", "Python, FastAPI", "HTTP surface, authentication seam, serves the frontend"],
        ["Worker", "Celery", "Executes the analysis pipeline off the request path"],
        ["Database", "PostgreSQL", "Matches, metrics, events, users, links, messages"],
        ["Broker and bus", "Redis", "Task queue and live progress publication"],
        ["Object storage", "S3 compatible", "Recordings, frames, generated clips"],
        ["Frontend", "Static HTML, CSS and JavaScript", "Served by the API on the same origin"],
    ], [30 * mm, 34 * mm, FULL - 64 * mm])

    yield P("Request and analysis flow", "h2")
    yield code(
        "client  ->  POST /api/matches                 create the match record\n"
        "        ->  POST /api/matches/{id}/source     upload the recording\n"
        "        ->  POST /api/matches/{id}/complete   enqueue the analysis\n"
        "\n"
        "worker  ->  load source from object storage\n"
        "        ->  run stages in cost order, publishing progress\n"
        "        ->  persist outcome, metrics, events, insights, cost\n"
        "\n"
        "client  ->  GET  /api/matches/{id}/progress   live stream of stage events\n"
        "        ->  GET  /api/matches/{id}            the finished report")

    yield P("Live progress", "h2")
    yield P(
        "Progress is published to a per match channel and streamed to the browser. It is "
        "explicitly transient and not persisted: it describes the run rather than the "
        "result, and the result is stored separately. This keeps a long analysis "
        "observable without adding write load for information that has no value once the "
        "match is complete.")

    yield P("Frontend", "h2")
    yield P(
        "The interface is intentionally plain static assets served by the API on the same "
        "origin, avoiding a cross origin configuration and a separate deployment. There is "
        "no build step, so edits are live on refresh.")

    # ---------------- 16 ----------------
    yield P("16. API reference", "h1")
    yield P("All endpoints are prefixed with /api and return JSON unless stated otherwise.")
    yield P("Identity and account", "h3")
    yield from table([
        ["Method and path", "Purpose"],
        ["POST /auth/signup", "Create an account, declaring player or coach"],
        ["POST /auth/signin", "Authenticate"],
        ["GET /account", "Current profile"],
        ["PATCH /account", "Update profile fields"],
        ["GET /account/skill-survey", "Questions supplied by the adapter"],
        ["POST /account/suggest-level", "Map survey answers to a suggested level"],
        ["POST /account/avatar", "Upload a profile image, optional"],
        ["DELETE /account/avatar", "Remove the profile image"],
    ], [58 * mm, FULL - 58 * mm])
    yield P("Matches and analysis", "h3")
    yield from table([
        ["Method and path", "Purpose"],
        ["GET /games", "Registered games and editions"],
        ["POST /matches", "Create a match record"],
        ["POST /matches/{id}/frames", "Upload extracted frames, optical path"],
        ["POST /matches/{id}/source", "Upload a recording or replay export"],
        ["POST /matches/{id}/complete", "Enqueue analysis"],
        ["GET /matches", "List the caller's matches"],
        ["GET /matches/{id}", "Full match, report and metrics"],
        ["GET /matches/{id}/progress", "Live progress stream"],
        ["GET /matches/{id}/report.pdf", "Report as a PDF"],
        ["GET /matches/{id}/frame", "A supporting frame, path checked"],
        ["GET /matches/{id}/clip", "A generated highlight clip, path checked"],
        ["GET /matches/{id}/video", "The source recording"],
        ["GET /matches/trends/{game}/{edition}", "Metric trends, optionally windowed"],
        ["GET /matches/patterns/{game}/{edition}", "Recurring weaknesses across matches"],
    ], [58 * mm, FULL - 58 * mm])
    yield P("Coaching", "h3")
    yield from table([
        ["Method and path", "Purpose"],
        ["GET /coaches", "Public coach directory"],
        ["GET /coaches/{id}", "Coach profile, allow listed fields only"],
        ["POST /coaches/{id}/connect", "Player requests this coach"],
        ["POST /coaches/{id}/disconnect", "Player ends the relationship"],
        ["POST /coaches/{id}/sharing", "Player sets which reports are shared"],
        ["GET /requests", "Coach lists pending requests"],
        ["POST /requests/{player}/{decision}", "Coach accepts or declines"],
        ["GET /clients", "Coach lists accepted clients"],
        ["GET /clients/{player}", "Client summary and aggregated weaknesses"],
        ["GET /clients/{player}/report/{match}", "A shared report"],
        ["GET /clients/{player}/report/{match}.pdf", "A shared report as a PDF"],
        ["GET and POST /chat/{peer}", "Read and send messages"],
        ["GET /chat/threads", "Conversation list with unread counts"],
        ["GET and POST /checklist", "Shared improvement checklist"],
        ["GET /usage", "Matches analysed against the allowance"],
    ], [58 * mm, FULL - 58 * mm])
    yield P(
        "Ordering note for implementers: the PDF route for a shared report must be "
        "registered before the JSON route, otherwise the match identifier pattern captures "
        "the file extension and the PDF route becomes unreachable.", "note")

    # ---------------- 17 ----------------
    yield P("17. Persistence and object storage", "h1")
    yield from table([
        ["Table", "Holds"],
        ["matches", "Match records, outcome, capture context, cost, status, report payload"],
        ["match_metrics", "Individual metric values with provenance and confidence"],
        ["match_events", "Timestamped events with supporting frame references"],
        ["users", "Accounts, role, profile, coach specific fields, survey answers"],
        ["coach_links", "Player to coach relationships and their acceptance state"],
        ["messages", "Chat between linked parties"],
        ["usage_counters", "Matches analysed per identity, for allowance enforcement"],
    ], [38 * mm, FULL - 38 * mm])
    yield P(
        "Large binary data never enters the database. Recordings, extracted frames and "
        "generated clips live in object storage and are referenced by key. Media is served "
        "through endpoints that validate the requested key against the match, so a key "
        "cannot be manipulated to read another match's data.")
    yield P(
        "Schema changes are applied as idempotent statements rather than through a migration "
        "framework. One operational lesson is recorded explicitly in the codebase: adding a "
        "column with a default value backfills that default into every existing row. When "
        "the column expressed an acceptance state, this silently rewrote live relationships. "
        "The correct sequence is to add the column without a default, backfill deliberately, "
        "and only then set the default for future rows.", "note")

    # ---------------- 18 ----------------
    yield P("18. Security, privacy and identity", "h1")
    yield bullets([
        "<b>Authentication is a seam, not an implementation.</b> Identity resolution is "
        "isolated behind a single dependency so that a hosted authentication provider can be "
        "inserted without touching business logic. Rolling a bespoke authentication system "
        "is explicitly rejected in the design notes.",
        "<b>Least privilege by construction.</b> Coach visible player data is assembled from "
        "an explicit allow list. Fields are included deliberately rather than excluded by "
        "memory, so a newly added sensitive field is private by default.",
        "<b>Media access is path checked.</b> Frame, clip and recording endpoints validate "
        "the requested key against the match being requested.",
        "<b>Message length is bounded</b> at the storage layer rather than only in the "
        "interface.",
        "<b>Usage is metered per identity</b> with a configurable allowance, returning a "
        "distinct payment required status when exhausted rather than failing ambiguously.",
        "<b>Consent is required before any sharing</b>, and is revocable by the player at "
        "any time.",
    ])
    yield P(
        "Known gap, stated plainly: production grade authentication has not yet been "
        "connected to the seam. The seam exists and is respected throughout the codebase, "
        "but the current identity resolution is a development mechanism and must be replaced "
        "before public deployment.", "note")

    # ---------------- 19 ----------------
    yield P("19. Quality assurance", "h1")
    yield P(
        "The test suite runs without a database, without optical character recognition "
        "dependencies and without network access or API credentials. Model behaviour is "
        "simulated by scripted offline implementations, so tests are deterministic and free "
        "to run.")
    yield from table([
        ["Test area", "What it protects"],
        ["Architectural guard", "Fails the build if any game identifier appears in the core"],
        ["Score interpretation", "The layered defences against phantom readings, including "
                                 "the reproduced high scoring failure"],
        ["Scoreboard batching", "That batched readings map to the correct moment even when "
                                "a response is short or reordered"],
        ["Result consistency", "That the match title and the report body cannot state "
                               "different scores"],
        ["Report schema", "That the stored payload and the requested schema cannot drift "
                          "apart"],
        ["Pipeline stages", "Event mapping, exclusions and correct halting on budget breach"],
        ["Second game", "That a replay based game runs through the unmodified core"],
    ], [40 * mm, FULL - 40 * mm])
    yield P(
        "One class of defect drove a specific test design. The stored report payload was "
        "assembled field by field in more than one place, so adding a section to the schema "
        "produced a model that answered it and a writer that silently discarded it. The "
        "result was a report shorter than the one it replaced, with no error anywhere. Tests "
        "now assert that the schema and the persisted field list cannot diverge.", "note")

    # ---------------- 20 ----------------
    yield P("20. Configuration reference", "h1")
    yield P(
        "Behaviour is configured by environment variables. The table below lists the "
        "settings that materially change cost, speed or output quality.")
    yield from table([
        ["Setting", "Effect", "Default"],
        ["Match budget", "Hard cost ceiling per fifteen minutes, scaled by duration", "0.25 USD"],
        ["Free match limit", "Matches per identity before payment is required", "25"],
        ["Video two pass", "Multiple independent viewings with consensus filtering", "Off"],
        ["Watch passes", "Number of independent viewings in multi pass mode", "2"],
        ["Score read", "Deterministic scoreboard timeline for score and goal timing", "On"],
        ["Score max frames", "Sampling density of the scoreboard read", "60"],
        ["Score batch", "Scoreboard crops sent per request", "6"],
        ["Score workers", "Concurrent scoreboard requests", "12"],
        ["Video compression", "Downscale before upload to cut transfer time", "On"],
        ["Media resolution", "Detail level the video model is asked to use", "Medium"],
        ["Deep goals", "Close re read of the seconds around each goal", "Configurable"],
        ["Highlights", "Assemble local clips around important events", "On"],
        ["Self learning", "Research knowledge gaps and record sourced facts", "Configurable"],
        ["Vision engine", "Cloud model, local model, or offline stub", "Stub"],
    ], [34 * mm, FULL - 60 * mm, 26 * mm])
    yield P(
        "Sampling density deserves a note, because it is the setting most likely to be "
        "adjusted for the wrong reason. It was originally reduced to save time, at a point "
        "when each reading was an individual request. Once readings were batched into "
        "parallel rounds the time argument disappeared, but the reduced density remained and "
        "became the direct cause of a scoring error. Density is now understood as an accuracy "
        "setting rather than a performance one.", "note")

    # ---------------- 21 ----------------
    yield P("21. Operational runbook", "h1")
    yield P("Standard operations", "h3")
    yield code(
        "docker compose up -d                        start the stack\n"
        "docker compose run --rm api pytest -q       run the test suite\n"
        "docker compose restart worker               reload after pipeline changes\n"
        "docker compose run --rm api python -m tools.reset_data      clear match data")
    yield P("Operational cautions", "h3")
    yield bullets([
        "<b>The worker does not reload automatically.</b> After changing pipeline or adapter "
        "code the worker must be restarted, or it will continue executing the previous "
        "version while appearing healthy. This is the single most common source of confusing "
        "results during development.",
        "<b>Truncating tables directly can hang</b> on a table lock. The provided reset tool "
        "takes a weaker lock and applies a timeout.",
        "<b>Optical character recognition dependencies are version pinned</b> for "
        "compatibility reasons documented at the pin site. Read those notes before "
        "upgrading.",
        "<b>Test interactive flows with disposable accounts.</b> Automated browser sessions "
        "left running have previously triggered real state changes against live accounts.",
    ])

    # ---------------- 22 ----------------
    yield P("22. Current status, limitations and roadmap", "h1")
    yield P("Validated", "h2")
    yield bullets([
        "Full match analysis end to end, producing a structured coaching report with "
        "timestamps that jump to the moment.",
        "Correct score and complete goal timeline recovered deterministically from footage, "
        "verified against a known result including a goal scored in stoppage time.",
        "The game agnostic abstraction, demonstrated by a second game with entirely "
        "different properties running through an unmodified core.",
        "Cost ceiling enforcement, with a per match breakdown of spend.",
        "Coach and player roles, the consent model, shared workspace and report sharing.",
        "Analysis time reduced from 295 seconds to approximately 120 on a real match, with "
        "identical output.",
    ])
    yield P("Known limitations", "h2")
    yield from table([
        ["Limitation", "Consequence", "Status"],
        ["Production authentication not connected",
         "Not deployable publicly in the current state", "Seam ready, provider pending"],
        ["Progress chart baseline moves with the window",
         "Genuine improvement is not visible", "Diagnosed, correction identified"],
        ["Counting metrics are model estimates",
         "Shot and chance counts are not reliable", "Candidates for removal or labelling"],
        ["Score derived metrics not sourced from the deterministic read",
         "Correct today by model competence rather than by construction", "Straightforward fix"],
        ["Occasional empty model responses",
         "A retry adds roughly a minute when it occurs", "Observed in two of three runs"],
        ["Statistics screen regions not calibrated",
         "Possession and shot regions are placeholders in the optical path", "Needs sample footage"],
    ], [46 * mm, 52 * mm, FULL - 98 * mm])
    yield P("Direction", "h2")
    yield bullets([
        "Connect a hosted authentication provider at the existing seam, then accounts and "
        "billing.",
        "Correct the progress layer: a fixed baseline, window against window comparison, and "
        "honest presentation of sample size and metric confidence.",
        "Derive score based metrics from the deterministic read rather than from the model.",
        "Deliver reports by email in addition to in application download.",
        "Extend coach tooling: editable coaching points and drill assignment.",
        "Add further games, which the architecture treats as plugins rather than projects.",
    ])

    # ---------------- appendix ----------------
    yield P("Appendix A. Glossary", "h1")
    yield from table([
        ["Term", "Definition"],
        ["Adapter", "A plugin encapsulating everything specific to one game and edition"],
        ["Core", "The game agnostic engine: domain model, pipeline, storage, progress"],
        ["Source type", "How data arrived: video, replay export, public API, screenshot"],
        ["Stage", "One step of the pipeline, executed in cost order and independently "
                  "switchable"],
        ["Interpretation", "The adapter step turning raw readings into outcome, metrics and "
                           "events"],
        ["Knowledge layer", "Per game structured facts used to ground the coaching model"],
        ["Plateau", "A run of consecutive equal readings, used to distinguish a real value "
                    "from a transient misreading"],
        ["Consensus filter", "Retaining only observations that recur across independent "
                             "viewings"],
        ["Link", "A player to coach relationship, created by the player and accepted by the "
                 "coach"],
        ["Seam", "A deliberate isolation point where an external provider can be inserted"],
    ], [34 * mm, FULL - 34 * mm])
    yield Spacer(1, 8)
    yield rule()
    yield Spacer(1, 4)
    yield P(f"{TITLE}. {SUBTITLE}. {VERSION}, {y}.", "cap")


if __name__ == "__main__":
    out = build(Path("docs/Coachfio-Technical-Documentation.pdf"))
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
