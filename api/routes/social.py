"""Coach directory, player<->coach links, chat, and the practice checkboard.

Access model in one paragraph: the PLAYER creates every link (that act is the
consent), chat only exists across a link, and a coach can read a linked player's
progress SUMMARY (form, recurring issues, per-match one-liners) - never the raw
match objects, so `_owned()` on the matches routes stays the single owner gate
for full data. Identity is whatever `current_user` returns; when the hosted auth
provider lands at that seam, nothing here changes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import db_session, require_user
from core.storage import social
from core.storage.repository import MatchRepository
from core.storage.models import UserRow
from core.storage.users import get_or_create_user

router = APIRouter(prefix="/api", tags=["social"])


def _public(row) -> dict:
    """What one account may see of another. Deliberately enumerated rather than
    filtered: email, survey answers and usage must never leak into a listing, and
    an allow-list makes that a decision rather than an oversight."""
    cp = dict(getattr(row, "coach_profile", None) or {})
    return {
        "user_id": row.user_id,
        "display_name": row.display_name or "Coach",
        "role": getattr(row, "role", "player") or "player",
        "avatar_url": (f"/api/users/{row.user_id}/avatar"
                       if getattr(row, "avatar", "") else None),
        # Enough to choose from in a list; the rest is on the coach's page.
        "headline": cp.get("headline", ""),
        "specialties": list(cp.get("specialties") or [])[:6],
        "price": cp.get("price", ""),
        "currency": cp.get("currency", "EUR"),
    }


# ---- directory + links -----------------------------------------------------

@router.get("/coaches")
def list_coaches(session: Session = Depends(db_session),
                 user: str = Depends(require_user)) -> dict:
    me = get_or_create_user(session, user)
    status = social.status_map(session, user)
    return {
        "coaches": [
            {**_public(c),
             "status": status.get(c.user_id, "none"),
             "connected": status.get(c.user_id) == social.ACCEPTED}
            for c in social.coach_directory(session)
            if c.user_id != user
        ],
        "role": getattr(me, "role", "player") or "player",
    }


@router.get("/coaches/{coach_id}")
def coach_detail(coach_id: str, session: Session = Depends(db_session),
                 user: str = Depends(require_user)) -> dict:
    """A coach's public page. Readable by any signed-in account - a player has to
    be able to look before asking - but it returns only the profile a coach
    wrote for exactly this purpose, never their account data."""
    row = session.get(UserRow, coach_id)
    if row is None or (getattr(row, "role", "player") != "coach"):
        raise HTTPException(404, "no such coach")
    cp = dict(getattr(row, "coach_profile", None) or {})
    return {
        **_public(row),
        "bio": cp.get("bio", ""),
        "experience": cp.get("experience", ""),
        "package_title": cp.get("package_title", ""),
        "includes": list(cp.get("includes") or [])[:8],
        "clients": len(social.clients_of(session, coach_id)),
        "status": social.link_status(session, coach_id=coach_id, player_id=user) or "none",
    }


@router.post("/coaches/{coach_id}/connect")
def request_coach(coach_id: str, session: Session = Depends(db_session),
                  user: str = Depends(require_user)) -> dict:
    """Sends a REQUEST. Nothing is shared and no chat opens until the coach
    accepts - this endpoint deliberately grants nothing on its own."""
    try:
        row = social.request_coach(session, player_id=user, coach_id=coach_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"coach_id": coach_id, "status": getattr(row, "status", social.PENDING)}


@router.get("/requests")
def list_requests(session: Session = Depends(db_session),
                  user: str = Depends(require_user)) -> dict:
    """Players waiting on this coach's decision."""
    me = get_or_create_user(session, user)
    if (getattr(me, "role", "player") or "player") != "coach":
        raise HTTPException(403, "only coach accounts receive requests")
    return {
        "requests": [
            {**_public(p),
             "display_name": p.display_name or (p.email or "Player").split("@")[0],
             "skill_level": p.skill_level}
            for p in social.pending_for_coach(session, user)
        ]
    }


@router.post("/requests/{player_id}/{decision}")
def respond_request(player_id: str, decision: str,
                    session: Session = Depends(db_session),
                    user: str = Depends(require_user)) -> dict:
    if decision not in ("accept", "decline"):
        raise HTTPException(422, "decision must be accept or decline")
    ok = social.respond_to_request(session, coach_id=user, player_id=player_id,
                                   accept=(decision == "accept"))
    if not ok:
        raise HTTPException(404, "no pending request from that player")
    return {"player_id": player_id, "status": "accepted" if decision == "accept" else "declined"}


@router.post("/coaches/{coach_id}/disconnect")
def disconnect_coach(coach_id: str, session: Session = Depends(db_session),
                     user: str = Depends(require_user)) -> dict:
    return {"connected": False,
            "removed": social.disconnect(session, player_id=user, coach_id=coach_id)}


class SharingIn(BaseModel):
    share_reports: bool


@router.post("/coaches/{coach_id}/sharing")
def set_sharing(coach_id: str, body: SharingIn, session: Session = Depends(db_session),
                user: str = Depends(require_user)) -> dict:
    """Player grants or revokes full-report access. `user` is always the player
    side of the link, so a coach calling this can only ever change a link where
    THEY are the player - i.e. their own coach, not their client."""
    if not social.set_sharing(session, player_id=user, coach_id=coach_id,
                              share=body.share_reports):
        raise HTTPException(404, "you are not connected to that coach")
    return {"coach_id": coach_id, "share_reports": body.share_reports}


# ---- the coach's client list ----------------------------------------------

def _match_brief(m) -> dict:
    rep = next((i for i in (m.insights or []) if i.kind == "coaching_report"), None)
    return {
        "id": m.id,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        # Player-first: a coach reading a client's match wants it the way the
        # client experienced it, and the brief carries no side to derive it from.
        "score": m.scoreline(),
        "result": (m.outcome or {}).get("result"),
        "takeaway": (rep.summary if rep else "") or "",
    }


def _progress(session: Session, player_id: str) -> dict:
    """The summary a linked coach sees: recent results + what keeps recurring.
    Deliberately NOT the full match payloads."""
    from adapters.base.registry import UnknownGameError, registry

    repo = MatchRepository(session)
    matches = [m for m in repo.list(identity=player_id, limit=10)
               if m.status.value == "complete"]
    issues = repo.recurring_issues(player_id, limit=8) or {}

    # Weakness tags are adapter vocabulary ids; the human label lives with the
    # adapter of the game the player actually plays.
    labels: dict = {}
    if matches:
        try:
            for v in registry.get(matches[0].game_id, matches[0].game_edition).issue_vocabulary():
                labels[v.get("tag")] = v.get("label")
        except UnknownGameError:
            pass
    return {
        "matches": [_match_brief(m) for m in matches],
        "issues": [
            {**i, "label": labels.get(i.get("tag")) or str(i.get("tag", "")).replace("_", " ")}
            for i in (issues.get("issues") or [])[:4]
        ],
        "analysed": issues.get("matches", 0),
    }


@router.get("/clients")
def list_clients(session: Session = Depends(db_session),
                 user: str = Depends(require_user)) -> dict:
    me = get_or_create_user(session, user)
    if (getattr(me, "role", "player") or "player") != "coach":
        raise HTTPException(403, "only coach accounts have clients")
    unread = social.unread_counts(session, user)
    out = []
    for p in social.clients_of(session, user):
        out.append({
            **_public(p),
            "display_name": p.display_name or (p.email or "Player").split("@")[0],
            "skill_level": p.skill_level,
            "unread": unread.get(p.user_id, 0),
            "reports_shared": social.reports_shared(session, user, p.user_id),
            **_progress(session, p.user_id),
        })
    return {"clients": out}


@router.get("/clients/{player_id}")
def client_detail(player_id: str, session: Session = Depends(db_session),
                  user: str = Depends(require_user)) -> dict:
    if not social.linked(session, coach_id=user, player_id=player_id):
        # 404, not 403 - a 403 confirms the account exists.
        raise HTTPException(404, "no such client")
    p = get_or_create_user(session, player_id)
    return {**_public(p), "skill_level": p.skill_level,
            **_progress(session, player_id)}


@router.get("/clients/{player_id}/report/{match_id}.pdf")
async def client_report_pdf(player_id: str, match_id: str,
                            session: Session = Depends(db_session),
                            user: str = Depends(require_user)) -> Response:
    """Same three gates as the JSON report. Exists because the report page's
    Download button would otherwise point at the owner-only match route and 404
    for the very coach the player just granted access to."""
    from fastapi.concurrency import run_in_threadpool

    from core.report import build_match_report_pdf, report_filename

    if not social.linked(session, coach_id=user, player_id=player_id):
        raise HTTPException(404, "no such client")
    if not social.reports_shared(session, coach_id=user, player_id=player_id):
        raise HTTPException(403, "this player has not shared their reports with you")
    match = MatchRepository(session).get(match_id)
    if match is None or (match.capture or {}).get("identity") != player_id:
        raise HTTPException(404, "match not found")
    owner = get_or_create_user(session, player_id)
    pdf = await run_in_threadpool(build_match_report_pdf, match,
                                  player_name=owner.display_name or "")
    if pdf is None:
        raise HTTPException(404, "this match has no coaching report")
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report_filename(match)}"',
                 "Content-Length": str(len(pdf))},
    )


# NOTE: declared AFTER the .pdf route on purpose. FastAPI matches in registration
# order and `{match_id}` happily swallows "abc.pdf", so registering this first
# made every PDF request 404 as a missing match.
@router.get("/clients/{player_id}/report/{match_id}")
def client_report(player_id: str, match_id: str,
                  session: Session = Depends(db_session),
                  user: str = Depends(require_user)) -> dict:
    """Full report for a client's match - the ONLY way a coach reaches match
    detail. Three gates, all required: linked, the player granted report sharing,
    and the match actually belongs to that player. `/api/matches/{id}` stays
    owner-only, so this cannot be used to widen it.
    """
    if not social.linked(session, coach_id=user, player_id=player_id):
        raise HTTPException(404, "no such client")
    if not social.reports_shared(session, coach_id=user, player_id=player_id):
        raise HTTPException(403, "this player has not shared their reports with you")
    match = MatchRepository(session).get(match_id)
    if match is None or (match.capture or {}).get("identity") != player_id:
        raise HTTPException(404, "match not found")
    return match.model_dump(mode="json")


# ---- chat ------------------------------------------------------------------

class MessageIn(BaseModel):
    body: str


@router.get("/chat/threads")
def chat_threads(session: Session = Depends(db_session),
                 user: str = Depends(require_user)) -> dict:
    me = get_or_create_user(session, user)
    role = getattr(me, "role", "player") or "player"
    peers = (social.clients_of(session, user) if role == "coach"
             else social.coaches_of(session, user))
    unread = social.unread_counts(session, user)
    sharing = {} if role == "coach" else social.sharing_map(session, user)
    return {
        "role": role,
        "threads": [
            {**_public(p),
             "display_name": p.display_name or (p.email or "?").split("@")[0],
             "unread": unread.get(p.user_id, 0),
             "share_reports": sharing.get(p.user_id, False)}
            for p in peers
        ],
    }


@router.get("/chat/{peer}")
def chat_thread(peer: str, session: Session = Depends(db_session),
                user: str = Depends(require_user)) -> dict:
    if not social.can_chat(session, user, peer):
        raise HTTPException(404, "no conversation with that account")
    msgs = social.thread(session, me=user, peer=peer)
    return {"messages": [social.message_dict(m, user) for m in msgs]}


@router.post("/chat/{peer}")
def chat_send(peer: str, body: MessageIn, session: Session = Depends(db_session),
              user: str = Depends(require_user)) -> dict:
    try:
        m = social.send_message(session, sender=user, recipient=peer, body=body.body)
    except PermissionError:
        raise HTTPException(404, "no conversation with that account")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"message": social.message_dict(m, user)}


# ---- practice checkboard ---------------------------------------------------

class CheckIn(BaseModel):
    key: str
    done: bool


@router.get("/checklist")
def get_checklist(session: Session = Depends(db_session),
                  user: str = Depends(require_user)) -> dict:
    row = get_or_create_user(session, user)
    return {"checklist": dict(getattr(row, "checklist", None) or {})}


@router.post("/checklist")
def set_checklist(body: CheckIn, session: Session = Depends(db_session),
                  user: str = Depends(require_user)) -> dict:
    from datetime import datetime, timezone

    row = get_or_create_user(session, user)
    key = str(body.key or "").strip()[:120]
    if not key:
        raise HTTPException(422, "missing key")
    state = dict(getattr(row, "checklist", None) or {})
    state[key] = {"done": bool(body.done),
                  "ts": datetime.now(timezone.utc).isoformat()}
    row.checklist = state
    return {"checklist": state}
