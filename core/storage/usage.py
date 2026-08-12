"""Usage counters for plan limits (matches analysed per identity)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from core.storage.models import UsageRow


def get_usage(session: Session, user_id: str) -> int:
    row = session.get(UsageRow, user_id)
    return row.matches_analyzed if row else 0


def increment_usage(session: Session, user_id: str) -> int:
    row = session.get(UsageRow, user_id)
    if row is None:
        row = UsageRow(user_id=user_id, matches_analyzed=0)
        session.add(row)
    row.matches_analyzed += 1
    session.flush()
    return row.matches_analyzed
