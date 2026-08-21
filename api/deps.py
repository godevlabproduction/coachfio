from __future__ import annotations

from typing import Iterator

from fastapi import Header, HTTPException, Request
from sqlalchemy.orm import Session

from core.auth import read_session
from core.config import Settings
from core.storage.db import get_session

_settings = Settings()


def current_user(
    request: Request,
    x_user_id: str | None = Header(default=None),
) -> str:
    """Identity seam. Everything that owns data is keyed on what this returns.

    The signed session cookie is the only thing trusted. It is set after Supabase
    has verified who someone is (api/routes/auth.py), is HttpOnly so page scripts
    cannot read it, and is scoped to `.coachfio.com` in production so it covers
    the hub and every game subdomain.

    WHAT USED TO BE HERE, and why it is gone: an `X-User-Id` header and a `?u=`
    query parameter, both unverified. Either let any caller claim any account by
    knowing its id - no password, no token. That was tolerable while sign-in was
    equally forgeable; next to a real provider it is just a way around it. The
    frontend now sends neither: the cookie reaches the SSE stream, <video> and
    PDF links on its own.

    The header survives ONLY behind `ALLOW_DEV_USER_HEADER`, off by default, so
    the CLI and tools can act as a user without a browser. api/main.py refuses to
    start if it is on while session cookies are marked Secure.
    """
    user = read_session(request, _settings)
    if user:
        return user
    if _settings.allow_dev_user_header and x_user_id:
        return x_user_id.strip() or "anonymous"
    return "anonymous"


def require_user(
    request: Request,
    x_user_id: str | None = Header(default=None),
) -> str:
    """`current_user`, but refusing the shared "anonymous" identity once real
    sign-in exists.

    Every unsigned visitor resolves to the SAME literal identity, so with real
    accounts switched on, "anonymous" is one shared account: any unsigned
    visitor could upload matches and read every other unsigned visitor's
    matches and reports. While no provider is configured (local dev without
    keys) anonymous stays usable - it is the only identity there is.
    """
    user = current_user(request, x_user_id)
    if user == "anonymous" and _settings.supabase_enabled:
        raise HTTPException(401, "sign in to continue")
    return user


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
