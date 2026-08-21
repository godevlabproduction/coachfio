from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import db_session, require_user
from core.config import get_settings
from core.storage.usage import get_usage

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("")
def my_usage(session: Session = Depends(db_session), user: str = Depends(require_user)) -> dict:
    used = get_usage(session, user)
    limit = get_settings().free_match_limit
    return {
        "user_id": user,
        "matches_analyzed": used,
        "limit": limit,
        "remaining": max(0, limit - used),
    }
