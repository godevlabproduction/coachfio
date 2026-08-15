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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import db_session
from core.storage.users import (
    create_user,
    find_by_email,
    normalise_email,
    update_user,
    user_profile,
    valid_email,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
def sign_up(body: SignUpRequest, session: Session = Depends(db_session)) -> dict:
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
    return {"user_id": row.user_id, "profile": user_profile(row)}


@router.post("/signin")
def sign_in(body: SignInRequest, session: Session = Depends(db_session)) -> dict:
    email = normalise_email(body.email)
    if not valid_email(email):
        raise HTTPException(422, "Enter a valid email address.")
    row = find_by_email(session, email)
    if row is None:
        raise HTTPException(404, "No account found for that email.")
    return {"user_id": row.user_id, "profile": user_profile(row)}
