"""Sign up / sign in.

WHAT THIS IS NOT: it does not verify that you are who you say you are. There are
no passwords and no sessions - the project brief says to use a hosted auth
provider (Clerk / Auth0 / Supabase) rather than roll our own, and half-built
credential handling is worse than none.

WHAT THIS IS: real account records. Signing up creates a row keyed by email and
returns an opaque `user_id`; the client then presents that id at the
`current_user` seam and every match, report and usage counter is scoped to it.

Connecting a provider means one change: verify its token in `api/deps.current_user`
and return the subject claim instead of trusting the header. These endpoints then
either disappear or become thin wrappers around the provider's SDK - nothing that
stores or reads data has to change.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import current_user, db_session
from core.auth import (
    clear_session_cookie,
    make_handoff,
    read_handoff,
    set_session_cookie,
)
from core.config import Settings
from core.storage.users import (
    create_user,
    find_by_email,
    normalise_email,
    update_user,
    user_profile,
    valid_email,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_settings = Settings()


class SignUpRequest(BaseModel):
    email: str
    display_name: str | None = None
    skill_level: str | None = None
    # Adapter-defined answers (FC: Division Rivals tier, Champs record) kept so the
    # suggestion can be recomputed later and shown on the account page.
    skill_survey: dict | None = None
    # "player" (default) or "coach" - decides which home the app gives them.
    role: str | None = None


class SignInRequest(BaseModel):
    email: str


@router.post("/signup")
def sign_up(body: SignUpRequest, response: Response,
            session: Session = Depends(db_session)) -> dict:
    email = normalise_email(body.email)
    if not valid_email(email):
        raise HTTPException(422, "Enter a valid email address.")
    if find_by_email(session, email) is not None:
        raise HTTPException(409, "An account with that email already exists - sign in instead.")
    row = create_user(session, email, body.display_name, body.skill_level)
    if body.skill_survey:
        update_user(session, row.user_id, skill_survey=body.skill_survey)
    if body.role:
        update_user(session, row.user_id, role=body.role)
        row = find_by_email(session, email) or row
    set_session_cookie(response, row.user_id, _settings)
    # user_id is still returned so existing clients keep working while they move
    # over to the cookie. It stops being needed once nothing reads it.
    return {"user_id": row.user_id, "profile": user_profile(row)}


@router.post("/signin")
def sign_in(body: SignInRequest, response: Response,
            session: Session = Depends(db_session)) -> dict:
    email = normalise_email(body.email)
    if not valid_email(email):
        raise HTTPException(422, "Enter a valid email address.")
    row = find_by_email(session, email)
    if row is None:
        raise HTTPException(404, "No account found for that email.")
    set_session_cookie(response, row.user_id, _settings)
    return {"user_id": row.user_id, "profile": user_profile(row)}


@router.post("/signout")
def sign_out(response: Response) -> dict:
    """Drop the session cookie. Server-side because the cookie is HttpOnly and
    page scripts cannot delete it themselves - that is the point of HttpOnly."""
    clear_session_cookie(response, _settings)
    return {"ok": True}


class AdoptRequest(BaseModel):
    token: str


@router.post("/handoff")
def mint_handoff(user: str = Depends(current_user)) -> dict:
    """A 60-second token the hub can hand to a game subdomain.

    Needed only while one cookie cannot cover both hosts - locally, where the
    hosts are localhost and fifa.localhost. In production the cookie is scoped
    to .coachfio.com and nothing calls this.
    """
    if user == "anonymous":
        raise HTTPException(401, "not signed in")
    return {"token": make_handoff(user, _settings)}


@router.post("/handoff/adopt")
def adopt_handoff(body: AdoptRequest, response: Response,
                  session: Session = Depends(db_session)) -> dict:
    """Exchange a hand-off token for a session cookie on THIS origin.

    The token is verified here rather than trusted: it is signed, single-purpose
    (its own salt, so a session cookie cannot be presented as one, or vice
    versa) and expires in a minute.
    """
    user_id = read_handoff(body.token, _settings)
    if not user_id:
        raise HTTPException(401, "that sign-in link has expired - open the game again")
    from core.storage.users import get_or_create_user
    row = get_or_create_user(session, user_id)
    set_session_cookie(response, row.user_id, _settings)
    return {"ok": True, "profile": user_profile(row)}


@router.get("/session")
def whoami(user: str = Depends(current_user),
           session: Session = Depends(db_session)) -> dict:
    """Who the current cookie resolves to. The frontend calls this on load
    instead of reading an identity out of localStorage."""
    if user == "anonymous":
        return {"signed_in": False}
    from core.storage.users import get_or_create_user
    return {"signed_in": True, "profile": user_profile(get_or_create_user(session, user))}
