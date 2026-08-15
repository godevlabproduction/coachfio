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

_SIDES = {"home", "away"}
_ROLES = {"player", "coach"}
_CURRENCIES = {"EUR", "USD", "GBP"}
_CONTROL_SCHEMES = {"Classic", "Alternate"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


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
    if "preferred_side" in fields and fields["preferred_side"] in _SIDES:
        row.preferred_side = fields["preferred_side"]
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
        # Merge rather than replace, so answering one question does not wipe the
        # others. Values are coerced to short strings - this is opaque data the
        # adapter interprets, but it must not become a dumping ground.
        merged = dict(row.skill_survey or {})
        for k, v in fields["skill_survey"].items():
            merged[str(k)[:32]] = str(v)[:32] if v is not None else ""
        row.skill_survey = merged

    session.flush()
    return row


def user_profile(row: UserRow) -> dict:
    return {
        "user_id": row.user_id,
        "display_name": row.display_name,
        "email": row.email,
        "skill_level": row.skill_level,
        "control_scheme": row.control_scheme,
        "preferred_side": row.preferred_side,
        "skill_survey": dict(row.skill_survey or {}),
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
