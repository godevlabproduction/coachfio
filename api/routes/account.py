"""Account profile + usage.

Identity comes from `api/deps.current_user` - the auth seam. This router owns
the PROFILE (who the player is and how to pitch coaching at them), never
credentials.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from adapters.base.registry import UnknownGameError, registry
from api.deps import db_session, require_user
from core.config import get_settings
from core.models.enums import SkillLevel
from core.storage.objectstore import get_object_store
from core.storage.usage import get_usage
from core.storage.users import get_or_create_user, update_user, user_profile

router = APIRouter(prefix="/api/account", tags=["account"])


class ProfileUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None
    skill_level: str | None = None
    control_scheme: str | None = None
    skill_survey: dict | None = None
    role: str | None = None   # "player" | "coach" - validated in update_user
    coach_profile: dict | None = None   # public bio; only meaningful for coaches


class SuggestRequest(BaseModel):
    game_id: str = "ea-fc"
    edition: str = "26"
    answers: dict = {}


# Shown in the UI so the player understands what changing the level actually does.
SKILL_LEVELS = [
    {
        "value": SkillLevel.AMATEUR.value,
        "label": "Amateur",
        "blurb": "New or casual. Reports explain the basics in plain language, with no jargon.",
    },
    {
        "value": SkillLevel.INTERMEDIATE.value,
        "label": "Intermediate",
        "blurb": "You know the fundamentals. Reports assume them and focus on habits and decisions.",
    },
    {
        "value": SkillLevel.PRO.value,
        "label": "Pro",
        "blurb": "Competitive. Reports are dense, meta-aware and skip anything you already know.",
    },
]


def _adapter(game_id: str = "ea-fc", edition: str = "26"):
    """The survey questions and the mapping to a level are GAME-specific, so both
    come from the adapter; core and this router stay ignorant of divisions."""
    try:
        return registry.get(game_id, edition)
    except UnknownGameError:
        return None


def _payload(session: Session, user: str,
             game_id: str = "ea-fc", edition: str = "26") -> dict:
    row = get_or_create_user(session, user)
    limit = get_settings().free_match_limit
    used = get_usage(session, user)
    adapter = _adapter(game_id, edition)
    survey = adapter.skill_survey() if adapter else []
    # Answers are stored per game ("<game_id>@<edition>" in users.skill_survey);
    # this page is one game's account view, so only that game's answers apply.
    survey_key = f"{game_id}@{edition}"
    profile = user_profile(row, survey_key=survey_key)
    return {
        "profile": profile,
        "usage": {"used": used, "limit": limit, "remaining": max(0, limit - used)},
        "skill_levels": SKILL_LEVELS,
        "skill_survey": survey,
        "suggestion": (adapter.suggest_skill_level(profile["skill_survey"])
                       if adapter else None),
    }


@router.get("")
def read_account(session: Session = Depends(db_session),
                 user: str = Depends(require_user),
                 game_id: str = "ea-fc", edition: str = "26") -> dict:
    return _payload(session, user, game_id, edition)


@router.patch("")
def patch_account(body: ProfileUpdate,
                  session: Session = Depends(db_session),
                  user: str = Depends(require_user),
                  game_id: str = "ea-fc", edition: str = "26") -> dict:
    update_user(session, user, skill_survey_key=f"{game_id}@{edition}",
                **body.model_dump(exclude_unset=True))
    return _payload(session, user, game_id, edition)


@router.get("/skill-survey")
def read_skill_survey(game_id: str = "ea-fc", edition: str = "26") -> dict:
    """The survey questions alone. Public and read-only, so the sign-up screen can
    fetch them before an account exists - hitting GET /api/account for this would
    create an 'anonymous' profile row as a side effect."""
    adapter = _adapter(game_id, edition)
    if adapter is None:
        raise HTTPException(404, f"unknown game {game_id}@{edition}")
    return {"skill_survey": adapter.skill_survey()}


@router.post("/suggest-level")
def suggest_level(body: SuggestRequest) -> dict:
    """Suggest a coaching level from survey answers. No auth and no writes - it is
    used on the sign-up screen before an account exists, and the player is free to
    ignore the result."""
    adapter = _adapter(body.game_id, body.edition)
    if adapter is None:
        raise HTTPException(404, f"unknown game {body.game_id}@{body.edition}")
    return {"suggestion": adapter.suggest_skill_level(body.answers or {})}


# ---- profile picture -------------------------------------------------------
# Optional by design. Nothing requires one; the UI shows initials when there is
# no image, so the whole feature can be ignored and the app still looks right.

AVATAR_MAX_BYTES = 4 * 1024 * 1024
AVATAR_PX = 256


def _avatar_key(user_id: str) -> str:
    return f"avatars/{user_id}.jpg"


@router.post("/avatar")
async def upload_avatar(file: UploadFile = File(...),
                        session: Session = Depends(db_session),
                        user: str = Depends(require_user)) -> dict:
    """Accept an image, normalise it, store it.

    Re-encoding rather than storing the upload verbatim does three useful things
    at once: it caps the stored size, it guarantees the bytes really are an image
    (a renamed file fails to decode), and it drops EXIF - which on a phone photo
    carries GPS coordinates the user did not intend to share.
    """
    from io import BytesIO

    from PIL import Image, ImageOps

    raw = await file.read()
    if not raw:
        raise HTTPException(422, "empty file")
    if len(raw) > AVATAR_MAX_BYTES:
        raise HTTPException(413, "image too large - keep it under 4 MB")

    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except Exception:
        raise HTTPException(422, "that file is not an image we can read")

    # Honour the camera's rotation flag before cropping, or portrait photos come
    # out sideways.
    img = ImageOps.exif_transpose(img)
    img = ImageOps.fit(img.convert("RGB"), (AVATAR_PX, AVATAR_PX),
                       method=Image.LANCZOS, centering=(0.5, 0.4))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)

    key = _avatar_key(user)
    get_object_store().put(key, buf.getvalue(), content_type="image/jpeg")
    update_user(session, user, avatar=key)
    return {"avatar_url": f"/api/users/{user}/avatar"}


@router.delete("/avatar")
def delete_avatar(session: Session = Depends(db_session),
                  user: str = Depends(require_user)) -> dict:
    row = get_or_create_user(session, user)
    key = getattr(row, "avatar", "") or ""
    if key:
        try:
            # The store exposes delete_prefix, not delete; an exact key is a
            # prefix of itself, so this removes precisely the one object.
            get_object_store().delete_prefix(key)
        except Exception:  # noqa: BLE001 - the row must clear even if the object is gone
            pass
    update_user(session, user, avatar="")
    return {"avatar_url": None}


# Separate router: avatars are addressed by the OWNER's id, not the viewer's, so
# a coach can render their clients' pictures. Deliberately not secret - it is a
# profile picture inside the app, and an <img> cannot send an auth header.
users_router = APIRouter(prefix="/api/users", tags=["account"])


@users_router.get("/{user_id}/avatar")
def get_avatar(user_id: str, session: Session = Depends(db_session)) -> Response:
    row = get_or_create_user(session, user_id)
    key = getattr(row, "avatar", "") or ""
    if not key:
        raise HTTPException(404, "no avatar")
    try:
        data = get_object_store().get(key)
    except Exception:
        raise HTTPException(404, "no avatar")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=60"})
