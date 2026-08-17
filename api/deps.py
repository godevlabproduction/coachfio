from __future__ import annotations

from typing import Iterator

from fastapi import Header, Query, Request
from sqlalchemy.orm import Session

from core.auth import read_session
from core.config import Settings
from core.storage.db import get_session

_settings = Settings()


def current_user(
    request: Request,
    x_user_id: str | None = Header(default=None),
    u: str | None = Query(default=None),
) -> str:
    """Identity seam. The ONE place a hosted auth provider plugs in.

    Resolution order, most trustworthy first:

      1. The signed session cookie (core/auth/session.py). Set at sign-in,
         scoped to `.coachfio.com` in production so it covers the hub and every
         game subdomain, and sent automatically by the SSE stream, the <video>
         element and PDF links - none of which can set a header.
      2. `X-User-Id`, the development header. Trivially forged; it survives for
         the CLI, the tests and tools/. Harmless while (1) is equally forgeable
         at sign-in; it must be REMOVED the day a provider lands, or it becomes
         a bypass around the provider.
      3. `?u=`, for the same header-less callers as (1). Redundant now that the
         cookie reaches them, and kept only so links already in the wild do not
         break. Remove with (2).

    Connecting a provider means: verify its token, map the subject claim through
    `users.auth_subject` to OUR user_id, and return that. Nothing downstream
    changes - all 44 call sites and every ownership check stay as they are.
    """
    return (
        read_session(request, _settings)
        or (x_user_id or "").strip()
        or (u or "").strip()
        or "anonymous"
    )


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
