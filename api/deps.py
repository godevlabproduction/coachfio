from __future__ import annotations

from typing import Iterator

from fastapi import Header
from sqlalchemy.orm import Session

from core.storage.db import get_session


def current_user(x_user_id: str | None = Header(default=None)) -> str:
    """Identity seam. This is the ONE place to plug in a hosted auth provider
    (Clerk / Auth0 / Supabase, per the brief's 'use a provider, don't roll it
    yourself'): verify the bearer token here and return the user id. Until then,
    a dev `X-User-Id` header (default 'anonymous') stands in — usage limits are
    keyed on whatever this returns, so the swap needs no other changes."""
    return (x_user_id or "anonymous").strip() or "anonymous"


def db_session() -> Iterator[Session]:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
