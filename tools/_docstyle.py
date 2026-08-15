"""Shared light-theme document furniture for the generated PDFs.

Deliberately NOT the app theme. The in-app report PDF (core/report/pdf.py) is
dark because it is a product surface; these are documents, so they use a plain
light theme that prints and reads like documentation.

Both `tools/build_product_doc.py` (the full technical reference) and
`tools/build_overview_doc.py` (the short description) render through here, so the
two can never drift into different-looking documents.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, ListFlowable, ListItem, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

# ---- palette: neutral document, one accent ---------------------------------
INK = colors.HexColor("#12181F")
BODY = colors.HexColor("#2B3440")
MUTED = colors.HexColor("#5C6773")
ACCENT = colors.HexColor("#1B4F72")
RULE = colors.HexColor("#D3DAE0")
BAND = colors.HexColor("#F2F5F7")
CODEBG = colors.HexColor("#F7F9FA")

PAGE_W, PAGE_H = A4
MARGIN = 22 * mm
FULL = PAGE_W - 2 * MARGIN

_ss = getSampleStyleSheet()

S = {
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
    "lead": ParagraphStyle("lead", parent=_ss["BodyText"], fontName="Helvetica",
                           fontSize=11.4, leading=17, textColor=INK,
                           alignment=TA_JUSTIFY, spaceAfter=10),
    "bullet": ParagraphStyle("bullet", parent=_ss["BodyText"], fontName="Helvetica",
                             fontSize=9.6, leading=14.2, textColor=BODY, spaceAfter=3),
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
                              fontSize=40, leading=46, textColor=ACCENT, alignment=TA_CENTER),
    "cover_s": ParagraphStyle("cover_s", parent=_ss["Title"], fontName="Helvetica",
                              fontSize=14.5, leading=20, textColor=BODY, alignment=TA_CENTER),
    "cover_m": ParagraphStyle("cover_m", parent=_ss["Title"], fontName="Helvetica",
                              fontSize=9.5, leading=14, textColor=MUTED, alignment=TA_CENTER),
}


# ---- flowable helpers ------------------------------------------------------
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


def table(rows, widths, header=True):
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
    t.setStyle(TableStyle(style))
    return [t, Spacer(1, 10)]


def rule():
    t = Table([[""]], colWidths=[FULL], rowHeights=[1])
    t.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.7, RULE)]))
    return t


def callout(title, text):
    """A single boxed statement. Used sparingly, for the one claim on a page that
    a skimming reader must not miss."""
    inner = [Paragraph(f"<b>{title}</b>", ParagraphStyle(
        "ct", parent=S["bullet"], fontSize=9.4, leading=13, textColor=ACCENT, spaceAfter=3)),
        Paragraph(text, ParagraphStyle(
            "cb", parent=S["bullet"], fontSize=9.3, leading=13.6, textColor=BODY, spaceAfter=0))]
    t = Table([[inner]], colWidths=[FULL], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BAND),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return [t, Spacer(1, 11)]


# ---- document --------------------------------------------------------------
@dataclass
class DocMeta:
    title: str
    subtitle: str
    tagline: str
    version: str
    footer: str = "Confidential"


def cover(meta: DocMeta, dated: str):
    """Standard cover block. Yielded by a story before its first PageBreak."""
    yield Spacer(1, 52 * mm)
    yield P(meta.title, "cover_t")
    yield Spacer(1, 5 * mm)
    yield P(meta.subtitle, "cover_s")
    yield Spacer(1, 3 * mm)
    yield P(meta.tagline, "cover_m")
    yield Spacer(1, 42 * mm)
    yield rule()
    yield Spacer(1, 5 * mm)
    yield P(f"{meta.version}<br/>{dated}<br/>{meta.footer}", "cover_m")
    yield NextPageTemplate("body")
    yield PageBreak()


def render(path: Path, meta: DocMeta, blocks) -> Path:
    """Render `blocks` (an iterable of flowables) into a paginated document."""
    path.parent.mkdir(parents=True, exist_ok=True)

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
        canvas.drawString(MARGIN, PAGE_H - 13 * mm, f"{meta.title}  |  {meta.subtitle}")
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, PAGE_H - 15 * mm, PAGE_W - MARGIN, PAGE_H - 15 * mm)
        canvas.line(MARGIN, 15 * mm, PAGE_W - MARGIN, 15 * mm)
        canvas.drawString(MARGIN, 10.5 * mm, meta.version)
        canvas.drawRightString(PAGE_W - MARGIN, 10.5 * mm, str(canvas.getPageNumber()))
        canvas.restoreState()

    doc = BaseDocTemplate(
        str(path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"{meta.title} {meta.subtitle}", author=meta.title,
        subject=meta.tagline,
    )
    doc.addPageTemplates([
        PageTemplate(id="cover",
                     frames=[Frame(MARGIN, MARGIN, FULL, PAGE_H - 2 * MARGIN, id="cover")],
                     onPage=_cover_page),
        PageTemplate(id="body",
                     frames=[Frame(MARGIN, 19 * mm, FULL, PAGE_H - 40 * mm, id="body")],
                     onPage=_body_page),
    ])
    doc.build(list(blocks))
    return path
