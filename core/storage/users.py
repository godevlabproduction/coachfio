"""Account profiles.

Identity itself comes from the auth seam (`api/deps.current_user`); this module
only stores what the coach needs to know about a person. Deliberately holds no
credentials - password hashing and sessions belong to a hosted auth provider,
per the project brief.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models.enums import SkillLevel
from core.storage.models import UserRow

_ROLES = {"player", "coach"}
_CURRENCIES = {"EUR", "USD", "GBP"}
_CONTROL_SCHEMES = {"Classic", "Alternate"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def merge_survey_answers(existing: dict, survey_key: str, answers: dict) -> dict:
    """Merge one game's survey answers into the stored per-game shape.

    Stored shape: {"<game_id>@<edition>": {question: answer, ...}} - the key is
    an OPAQUE namespace here (core never interprets it; the API layer builds it
    from the adapter registry), it exists so two games' answers cannot collide.
    Merges rather than replaces at both levels, so answering one question does
    not wipe the others, and updating one game does not touch another's answers.
    Values are coerced to short strings - opaque data the adapter interprets,
    but not a dumping ground.
    """
    key = str(survey_key or "").strip()[:64]
    if not key:
        return dict(existing or {})
    nested = dict(existing or {})
    bucket = dict(nested.get(key) or {})
    for k, v in (answers or {}).items():
        bucket[str(k)[:32]] = str(v)[:32] if v is not None else ""
    nested[key] = bucket
    return nested


def normalise_email(email: object) -> str:
    return str(email or "").strip().lower()


def valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email or ""))


def find_by_email(session: Session, email: str) -> UserRow | None:
    """Case-insensitive lookup. Email is how a person finds their account again;
    `user_id` is the opaque key everything else is stored against, so swapping in
    a provider only changes how we arrive at that id."""
    email = normalise_email(email)
    if not email:
        return None
    stmt = select(UserRow).where(func.lower(UserRow.email) == email).limit(1)
    return session.execute(stmt).scalars().first()


def find_by_auth_subject(session: Session, subject: str) -> UserRow | None:
    """The account a provider login maps to, or None if it is not linked yet.

    This is the whole point of keeping `auth_subject` separate from `user_id`:
    the provider owns the subject, we own the id, and everything downstream
    (matches, usage, coach links) is keyed on the id we own.
    """
    subject = (subject or "").strip()
    if not subject:
        return None
    return session.query(UserRow).filter(UserRow.auth_subject == subject).one_or_none()


def link_auth_subject(session: Session, user_id: str, subject: str) -> UserRow:
    """Attach a provider login to an existing account.

    Refuses to move a subject that already points somewhere else: silently
    re-pointing it would hand one person's matches to another.
    """
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("auth subject is required")
    existing = find_by_auth_subject(session, subject)
    if existing is not None and existing.user_id != user_id:
        raise ValueError("that login is already linked to another account")
    row = get_or_create_user(session, user_id)
    row.auth_subject = subject
    session.flush()
    return row


def link_or_create_from_provider(session: Session, subject: str, email: str,
                                 email_verified: bool,
                                 display_name: str | None = None) -> UserRow:
    """Resolve a provider login to OUR account, creating one if needed.

    Three cases, in order:

    1. The subject is already linked - return that account. This is every sign-in
       after the first, and it is why the mapping exists.
    2. No link yet, but a VERIFIED email matches an existing account - adopt it.
       This is what stops an account created before the provider existed from
       being stranded with its matches. It requires the provider to have
       confirmed the address, because otherwise anyone could claim someone
       else's account by signing up with their email.
    3. Otherwise, a new account.

    An UNVERIFIED email never adopts. It gets a fresh account instead, which is
    the safe failure: a duplicate account is recoverable, a stolen one is not.
    """
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("provider gave no subject")

    existing = find_by_auth_subject(session, subject)
    if existing is not None:
        return existing

    email = normalise_email(email)
    if email and email_verified:
        row = find_by_email(session, email)
        if row is not None:
            return link_auth_subject(session, row.user_id, subject)

    row = create_user(session, email, display_name)
    return link_auth_subject(session, row.user_id, subject)


def create_user(session: Session, email: str, display_name: str | None = None,
                skill_level: object = None) -> UserRow:
    row = UserRow(
        user_id=uuid.uuid4().hex,
        email=normalise_email(email),
        display_name=(str(display_name).strip()[:80] or None) if display_name else None,
        skill_level=SkillLevel.parse(skill_level).value,
    )
    session.add(row)
    session.flush()
    return row


def get_or_create_user(session: Session, user_id: str) -> UserRow:
    """Every identity that reaches the API gets a profile on first sight, so the
    coach always has a skill level to calibrate against."""
    row = session.get(UserRow, user_id)
    if row is None:
        row = UserRow(user_id=user_id, skill_level=SkillLevel.INTERMEDIATE.value)
        session.add(row)
        session.flush()
    return row


def update_user(session: Session, user_id: str, **fields: Any) -> UserRow:
    """Patch a profile. Unknown keys and invalid values are ignored rather than
    raising, so a stale client cannot corrupt the coaching calibration."""
    row = get_or_create_user(session, user_id)

    if "skill_level" in fields and fields["skill_level"] is not None:
        row.skill_level = SkillLevel.parse(fields["skill_level"]).value
    if "control_scheme" in fields and fields["control_scheme"] in _CONTROL_SCHEMES:
        row.control_scheme = fields["control_scheme"]
    if "display_name" in fields and fields["display_name"] is not None:
        name = str(fields["display_name"]).strip()[:80]
        row.display_name = name or None
    if "email" in fields and fields["email"] is not None:
        email = str(fields["email"]).strip()[:255]
        row.email = email or None
    if "coach_profile" in fields and isinstance(fields["coach_profile"], dict):
        cp = dict(row.coach_profile or {})
        src = fields["coach_profile"]
        for k, cap in (("headline", 90), ("bio", 900), ("experience", 90)):
            if k in src:
                cp[k] = str(src[k] or "").strip()[:cap]
        # Price is stored as the digits the coach typed, with the currency
        # separate - no float maths, because nothing here charges anyone.
        if "price" in src:
            digits = "".join(ch for ch in str(src["price"] or "") if ch.isdigit() or ch in ".,")
            cp["price"] = digits[:10]
        if "currency" in src and src["currency"] in _CURRENCIES:
            cp["currency"] = src["currency"]
        if "package_title" in src:
            cp["package_title"] = str(src["package_title"] or "").strip()[:90]
        if "includes" in src:
            cp["includes"] = [str(x).strip()[:160] for x in (src["includes"] or [])
                              if str(x).strip()][:8]
        if "specialties" in src:
            # Capped in both directions: a coach listing twenty specialities is
            # advertising nothing.
            cp["specialties"] = [str(x).strip()[:40] for x in (src["specialties"] or [])
                                 if str(x).strip()][:6]
        row.coach_profile = cp
    if "avatar" in fields and fields["avatar"] is not None:
        row.avatar = str(fields["avatar"])[:200]
    if "role" in fields and fields["role"] in _ROLES:
        row.role = fields["role"]
    if "skill_survey" in fields and isinstance(fields["skill_survey"], dict):
        # Answers land under the caller-supplied "<game_id>@<edition>" namespace
        # (see merge_survey_answers). No key, no write: a survey answer that
        # cannot say which game it belongs to would corrupt another game's set.
        key = str(fields.get("skill_survey_key") or "").strip()
        if key:
            row.skill_survey = merge_survey_answers(
                row.skill_survey or {}, key, fields["skill_survey"])

    session.flush()
    return row


def user_profile(row: UserRow, survey_key: str | None = None) -> dict:
    """Serialize a profile. `survey_key` ("<game_id>@<edition>", opaque here)
    scopes `skill_survey` to that game's flat answers - what game pages render.
    Without it (hub/session contexts, which never show a survey) the full
    per-game mapping is returned under `skill_surveys` and `skill_survey` is
    empty rather than a guess: core cannot know which game a caller means."""
    nested = dict(row.skill_survey or {})
    return {
        "user_id": row.user_id,
        "display_name": row.display_name,
        "email": row.email,
        "skill_level": row.skill_level,
        "control_scheme": row.control_scheme,
        "skill_survey": dict(nested.get(survey_key) or {}) if survey_key else {},
        "skill_surveys": nested,
        "role": getattr(row, "role", None) or "player",
        "coach_profile": dict(getattr(row, "coach_profile", None) or {}),
        # A URL rather than the key: the client only needs somewhere to point an
        # <img> at, and the key is an internal detail.
        "avatar_url": (f"/api/users/{row.user_id}/avatar"
                       if getattr(row, "avatar", "") else None),
        # For "member since" on the profile. Serialised here rather than derived
        # client-side, because the client has no other source for it.
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
