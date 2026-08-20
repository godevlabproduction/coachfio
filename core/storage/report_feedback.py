"""Did the report help, and what was wrong with it?

The only place in the product where the coaching is judged. Everything else
improves what the coach SAYS; this is the one thing that can tell you whether
saying it was right.

Two consumers:

  1. The report page, so reopening it shows what you already said.
  2. The NEXT analysis. Recent complaints are put in front of the model with an
     instruction not to repeat the mistake - which is the whole point, and the
     reason the free-text note matters more than the star rating.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.storage.models import ReportFeedbackRow

MIN_RATING, MAX_RATING = 1, 5


def record(session: Session, *, user_id: str, match_id: str, rating: int,
           section: str = "", note: str = "") -> ReportFeedbackRow:
    """Save (or replace) this person's verdict on one report."""
    if not (MIN_RATING <= int(rating) <= MAX_RATING):
        raise ValueError(f"rating must be {MIN_RATING}-{MAX_RATING}")
    row = session.execute(
        select(ReportFeedbackRow).where(
            ReportFeedbackRow.user_id == user_id,
            ReportFeedbackRow.match_id == match_id)
    ).scalar_one_or_none()
    if row is None:
        row = ReportFeedbackRow(id=uuid4().hex, user_id=user_id, match_id=match_id)
        session.add(row)
    row.rating = int(rating)
    row.section = (section or "")[:48]
    row.note = (note or "").strip()[:2000]
    session.flush()
    return row


def for_match(session: Session, user_id: str, match_id: str) -> dict | None:
    row = session.execute(
        select(ReportFeedbackRow).where(
            ReportFeedbackRow.user_id == user_id,
            ReportFeedbackRow.match_id == match_id)
    ).scalar_one_or_none()
    if row is None:
        return None
    return {"rating": row.rating, "section": row.section, "note": row.note}


def recent_complaints(session: Session, user_id: str, limit: int = 4) -> list[dict]:
    """This player's recent criticism, newest first.

    Only rows that actually say something: a bare 5-star with no note tells the
    model nothing to act on, and padding the prompt with "they liked it" wastes
    the attention that should go on what to fix. Low ratings without a note are
    still included - "the defending section was rated 2/5" is a weak signal, but
    it is a signal.
    """
    rows = session.execute(
        select(ReportFeedbackRow)
        .where(ReportFeedbackRow.user_id == user_id)
        .order_by(ReportFeedbackRow.updated_at.desc())
        .limit(limit * 3)
    ).scalars().all()
    out = []
    for r in rows:
        if r.note or r.rating <= 3:
            out.append({"rating": r.rating, "section": r.section, "note": r.note})
        if len(out) >= limit:
            break
    return out
