"""Player <-> coach links and chat.

The consent model is the whole design: a link is ALWAYS created by the player,
and everything a coach can see or send flows through `linked()`. There is no
code path where a coach grants themselves access - remove that property and the
rest of this module becomes a data leak.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from core.storage.models import CoachLinkRow, MessageRow, UserRow

MESSAGE_MAX = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- links -----------------------------------------------------------------

ACCEPTED = "accepted"
PENDING = "pending"


def link_status(session: Session, coach_id: str, player_id: str) -> str | None:
    """None = no link at all, else "pending" | "accepted"."""
    row = session.get(CoachLinkRow, (coach_id, player_id))
    return None if row is None else (getattr(row, "status", None) or ACCEPTED)


def linked(session: Session, coach_id: str, player_id: str) -> bool:
    """ACCEPTED only. Every permission in this module funnels through here, so a
    pending request grants exactly nothing - no chat, no summary, no reports."""
    return link_status(session, coach_id, player_id) == ACCEPTED


def request_coach(session: Session, player_id: str, coach_id: str) -> CoachLinkRow:
    """Player asks a coach to take them on. Creates a PENDING row; the coach must
    accept before anything is shared. Idempotent - asking twice does not reset an
    already-accepted relationship back to pending."""
    target = session.get(UserRow, coach_id)
    if target is None or (getattr(target, "role", "player") != "coach"):
        raise ValueError("that account is not a coach")
    if player_id == coach_id:
        raise ValueError("you cannot connect to yourself")
    row = session.get(CoachLinkRow, (coach_id, player_id))
    if row is None:
        row = CoachLinkRow(coach_id=coach_id, player_id=player_id, status=PENDING)
        session.add(row)
        session.flush()
    return row


def respond_to_request(session: Session, coach_id: str, player_id: str,
                       accept: bool) -> bool:
    """Coach's decision. Declining DELETES the row rather than storing a refusal,
    so the player may ask again later - a permanent block would need to be an
    explicit feature, not a side effect of one 'not right now'."""
    row = session.get(CoachLinkRow, (coach_id, player_id))
    if row is None or (getattr(row, "status", None) or ACCEPTED) != PENDING:
        return False
    if accept:
        row.status = ACCEPTED
    else:
        session.delete(row)
    return True


def pending_for_coach(session: Session, coach_id: str) -> list[UserRow]:
    stmt = (
        select(UserRow)
        .join(CoachLinkRow, CoachLinkRow.player_id == UserRow.user_id)
        .where(CoachLinkRow.coach_id == coach_id, CoachLinkRow.status == PENDING)
        .order_by(CoachLinkRow.created_at)
    )
    return list(session.execute(stmt).scalars())


def status_map(session: Session, player_id: str) -> dict[str, str]:
    """{coach_id: status} for one player - lets the directory show 'Requested'."""
    stmt = select(CoachLinkRow).where(CoachLinkRow.player_id == player_id)
    return {r.coach_id: (getattr(r, "status", None) or ACCEPTED)
            for r in session.execute(stmt).scalars()}


def disconnect(session: Session, player_id: str, coach_id: str) -> bool:
    """Player withdraws the link (and with it the coach's read access)."""
    row = session.get(CoachLinkRow, (coach_id, player_id))
    if row is None:
        return False
    session.delete(row)
    return True


def set_sharing(session: Session, player_id: str, coach_id: str, share: bool) -> bool:
    """Only the PLAYER may change this - the caller is responsible for passing
    their own id as `player_id`. Returns False when no link exists."""
    row = session.get(CoachLinkRow, (coach_id, player_id))
    if row is None:
        return False
    row.share_reports = bool(share)
    return True


def reports_shared(session: Session, coach_id: str, player_id: str) -> bool:
    row = session.get(CoachLinkRow, (coach_id, player_id))
    if row is None or (getattr(row, "status", None) or ACCEPTED) != ACCEPTED:
        return False
    return bool(getattr(row, "share_reports", False))


def sharing_map(session: Session, player_id: str) -> dict[str, bool]:
    """{coach_id: shares_reports} for one player's links."""
    stmt = select(CoachLinkRow).where(CoachLinkRow.player_id == player_id)
    return {r.coach_id: bool(getattr(r, "share_reports", False))
            for r in session.execute(stmt).scalars()}


def coaches_of(session: Session, player_id: str) -> list[UserRow]:
    """Accepted coaches only - a pending request is not yet a relationship."""
    stmt = (
        select(UserRow)
        .join(CoachLinkRow, CoachLinkRow.coach_id == UserRow.user_id)
        .where(CoachLinkRow.player_id == player_id, CoachLinkRow.status == ACCEPTED)
        .order_by(CoachLinkRow.created_at)
    )
    return list(session.execute(stmt).scalars())


def clients_of(session: Session, coach_id: str) -> list[UserRow]:
    stmt = (
        select(UserRow)
        .join(CoachLinkRow, CoachLinkRow.player_id == UserRow.user_id)
        .where(CoachLinkRow.coach_id == coach_id, CoachLinkRow.status == ACCEPTED)
        .order_by(CoachLinkRow.created_at)
    )
    return list(session.execute(stmt).scalars())


def coach_directory(session: Session) -> list[UserRow]:
    """Every coach account. Small-N by assumption; paginate when that breaks."""
    stmt = select(UserRow).where(UserRow.role == "coach").order_by(UserRow.created_at)
    return list(session.execute(stmt).scalars())


# ---- chat ------------------------------------------------------------------

def can_chat(session: Session, a: str, b: str) -> bool:
    """Chat rides on a link, in either direction. No link, no channel - this is
    what keeps the directory from becoming a spam vector."""
    return linked(session, a, b) or linked(session, b, a)


def send_message(session: Session, sender: str, recipient: str, body: str) -> MessageRow:
    text = str(body or "").strip()[:MESSAGE_MAX]
    if not text:
        raise ValueError("empty message")
    if not can_chat(session, sender, recipient):
        raise PermissionError("no link between these accounts")
    row = MessageRow(id=uuid.uuid4().hex, sender=sender, recipient=recipient, body=text)
    session.add(row)
    session.flush()
    return row


def thread(session: Session, me: str, peer: str, limit: int = 100) -> list[MessageRow]:
    """Both directions, oldest first. Fetching marks the peer's messages read -
    reading IS the read receipt; there is no separate ack call to forget."""
    stmt = (
        select(MessageRow)
        .where(or_(
            (MessageRow.sender == me) & (MessageRow.recipient == peer),
            (MessageRow.sender == peer) & (MessageRow.recipient == me),
        ))
        .order_by(MessageRow.created_at.desc())
        .limit(limit)
    )
    rows = list(session.execute(stmt).scalars())[::-1]
    now = _now()
    for m in rows:
        if m.recipient == me and m.read_at is None:
            m.read_at = now
    return rows


def unread_counts(session: Session, me: str) -> dict[str, int]:
    """{peer_id: unread}, for badge rendering across a thread list."""
    stmt = (
        select(MessageRow.sender, func.count())
        .where(MessageRow.recipient == me, MessageRow.read_at.is_(None))
        .group_by(MessageRow.sender)
    )
    return {sender: int(n) for sender, n in session.execute(stmt)}


def message_dict(m: MessageRow, me: str) -> dict:
    return {
        "id": m.id,
        "mine": m.sender == me,
        "body": m.body,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }
