from __future__ import annotations

from typing import Iterator

from fastapi import Header, Query
from sqlalchemy.orm import Session

from core.storage.db import get_session


def current_user(
    x_user_id: str | None = Header(default=None),
    u: str | None = Query(default=None),
) -> str:
    """Identity seam. This is the ONE place to plug in a hosted auth provider
    (Clerk / Auth0 / Supabase, per the brief's 'use a provider, don't roll it
    yourself'): verify the bearer token here and return the user id. Until then,
    a dev `X-User-Id` header (default 'anonymous') stands in - usage limits,
    profiles and match ownership are all keyed on whatever this returns, so the
    swap needs no other changes.

    `?u=` is a fallback for ONE caller: the SSE progress stream. The browser's
    EventSource cannot set request headers, so the identity has to ride in the
    query string. It is no weaker than the dev header (both are trivially
    forged) - but when real auth lands, delete this parameter and give the
    stream a cookie or a short-lived token instead.
    """
    return (x_user_id or u or "anonymous").strip() or "anonymous"


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
