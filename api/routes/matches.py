from __future__ import annotations

import json

import redis.asyncio as aioredis
import re as _re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from adapters.base.registry import UnknownGameError, registry
from api.deps import current_user, db_session
from api.schemas import (
    CreateMatchRequest,
    CreateMatchResponse,
    FrameUploadResponse,
    TrendResponse,
)
from core.config import get_settings
from core.models.domain import Match
from core.models.enums import MatchStatus, SkillLevel
from core.progress.trends import build_trends
from core.report import build_match_report_pdf, report_filename
from core.models.enums import SourceType
from core.storage.frame_keys import frame_key, frame_prefix, source_key
from core.storage.objectstore import get_object_store
from core.storage.repository import MatchRepository
from core.storage.usage import get_usage, increment_usage
from core.storage.users import get_or_create_user

router = APIRouter(prefix="/api/matches", tags=["matches"])

_TERMINAL = {
    MatchStatus.COMPLETE.value,
    MatchStatus.FAILED.value,
    MatchStatus.OVER_BUDGET.value,
}


def is_terminal_event(parsed: dict) -> bool:
    """Does this progress event mean the RUN is over?

    Only the pipeline speaks for the match. Individual stages emit "failed" for
    recoverable things - a scoreboard read that didn't land, a roster OCR miss -
    and the run carries on. Treating those as terminal closed the SSE stream
    early; the client then re-read a still-"processing" match and announced a
    failure for a run that went on to succeed.
    """
    stage = parsed.get("stage")
    status = parsed.get("status")
    if parsed.get("match_status") in _TERMINAL:
        return True
    return stage == "pipeline" and (status in _TERMINAL or status == "final")


def _owned(repo: MatchRepository, match_id: str, user: str) -> Match:
    """Fetch a match that belongs to `user`, or 404.

    Deliberately 404 and not 403 for someone else's match: a 403 would confirm
    the id exists. Matches created before accounts have no `capture.identity`,
    so they are treated as unowned and remain readable rather than vanishing.
    """
    match = repo.get(match_id)
    if match is None:
        raise HTTPException(404, "match not found")
    owner = (match.capture or {}).get("identity")
    if owner and owner != user:
        raise HTTPException(404, "match not found")
    return match


@router.post("", response_model=CreateMatchResponse)
def create_match(
    body: CreateMatchRequest,
    session: Session = Depends(db_session),
    user: str = Depends(current_user),
) -> CreateMatchResponse:
    try:
        registry.get(body.game_id, body.edition)  # validate the game exists
    except UnknownGameError:
        raise HTTPException(404, f"unknown game {body.game_id}@{body.edition}")

    # Usage limit by matches analysed (per the brief - not by time).
    limit = get_settings().free_match_limit
    if get_usage(session, user) >= limit:
        raise HTTPException(402, f"match limit reached ({limit}); upgrade to analyse more")

    # Seed coaching calibration from the account, letting the request override it
    # for this one match. Doing it server-side means the report is always pitched
    # at the player's level even if the client forgets to send it.
    profile = get_or_create_user(session, user)
    capture = {
        "skill_level": profile.skill_level,
        "control_scheme": profile.control_scheme,
        "player_side": profile.preferred_side,
        **{k: v for k, v in (body.capture or {}).items() if v not in (None, "")},
        # Stamp the identity server-side so the coaching loop can track this player
        # across matches (the "learns you" longitudinal memory) and so every read
        # path can scope to the owner.
        "identity": user,
    }
    capture["skill_level"] = SkillLevel.parse(capture.get("skill_level")).value

    match = Match(
        game_id=body.game_id,
        game_edition=body.edition,
        source_type=body.source_type,
        capture=capture,
        status=MatchStatus.CREATED,
    )
    MatchRepository(session).save(match)
    increment_usage(session, user)
    return CreateMatchResponse(
        match_id=match.id,
        status=match.status.value,
        frames_endpoint=f"/api/matches/{match.id}/frames",
        complete_endpoint=f"/api/matches/{match.id}/complete",
        progress_endpoint=f"/api/matches/{match.id}/progress",
    )


@router.post("/{match_id}/frames", response_model=FrameUploadResponse)
def upload_frame(
    match_id: str,
    index: int = Form(...),
    timestamp_ms: int = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(db_session),
    user: str = Depends(current_user),
) -> FrameUploadResponse:
    # SYNC endpoint on purpose: FastAPI runs it in the threadpool, so the blocking
    # DB + object-store calls here never freeze the event loop (async handlers
    # doing sync IO stalled the whole API under concurrent uploads).
    repo = MatchRepository(session)
    match = _owned(repo, match_id, user)

    data = file.file.read()
    key = frame_key(match_id, index, timestamp_ms)
    get_object_store().put(key, data, content_type="image/jpeg")

    if match.status == MatchStatus.CREATED:
        repo.set_status(match_id, MatchStatus.UPLOADING)
    return FrameUploadResponse(match_id=match_id, index=index, key=key)


@router.post("/{match_id}/source", response_model=dict)
async def upload_source(
    match_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(db_session),
    user: str = Depends(current_user),
) -> dict:
    """Upload a non-video source (replay file / API export) for a replay/API
    match. The counterpart to per-frame upload on the video path."""
    repo = MatchRepository(session)
    match = _owned(repo, match_id, user)
    if match.source_type == SourceType.VIDEO:
        raise HTTPException(400, "this match is a video source; upload frames, not a source file")

    data = await file.read()
    key = source_key(match_id)
    await run_in_threadpool(get_object_store().put, key, data, "application/octet-stream")
    if match.status == MatchStatus.CREATED:
        repo.set_status(match_id, MatchStatus.UPLOADING)
    return {"match_id": match_id, "key": key, "bytes": len(data)}


@router.post("/{match_id}/complete")
def complete_upload(match_id: str, session: Session = Depends(db_session),
                    user: str = Depends(current_user)) -> dict:
    repo = MatchRepository(session)
    _owned(repo, match_id, user)
    repo.set_status(match_id, MatchStatus.QUEUED)
    session.commit()  # ensure the worker sees QUEUED before it runs

    # Import here to avoid the API importing Celery's task graph at module load.
    from workers.tasks import run_match_pipeline

    run_match_pipeline.delay(match_id)
    return {"match_id": match_id, "status": MatchStatus.QUEUED.value}


@router.get("/{match_id}")
def get_match(match_id: str, session: Session = Depends(db_session),
              user: str = Depends(current_user)) -> JSONResponse:
    match = _owned(MatchRepository(session), match_id, user)
    return JSONResponse(match.model_dump(mode="json"))


@router.get("/{match_id}/report.pdf")
async def get_report_pdf(match_id: str, session: Session = Depends(db_session),
                         user: str = Depends(current_user)) -> Response:
    """Download the coaching report as a PDF.

    A browser downloads this by navigating to the URL, which means no request
    headers - so the identity rides in `?u=`, the same fallback the progress
    stream and the video endpoint already use (see api/deps.current_user).

    Rendering is synchronous and CPU-bound, so it goes to the threadpool rather
    than stalling the event loop for every other request in flight.
    """
    match = _owned(MatchRepository(session), match_id, user)
    name = getattr(get_or_create_user(session, user), "display_name", "") or ""
    pdf = await run_in_threadpool(build_match_report_pdf, match, player_name=name)
    if pdf is None:
        raise HTTPException(404, "this match has no coaching report")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            # `inline` would open it in the browser's viewer; the ask was a
            # download. The filename is sanitised in report_filename().
            "Content-Disposition": f'attachment; filename="{report_filename(match)}"',
            "Content-Length": str(len(pdf)),
        },
    )


@router.get("/{match_id}/frame")
def get_frame(match_id: str, key: str, session: Session = Depends(db_session),
              user: str = Depends(current_user)) -> Response:
    """Serve one stored frame JPEG by object key (referenced from an event/insight
    `frame_refs`). Path-checked so it can only read this match's frames."""
    _owned(MatchRepository(session), match_id, user)
    if not key.startswith(frame_prefix(match_id)):
        raise HTTPException(403, "key does not belong to this match")
    try:
        data = get_object_store().get(key)
    except Exception:
        raise HTTPException(404, "frame not found")
    return Response(content=data, media_type="image/jpeg")


@router.get("/{match_id}/clip")
def get_clip(match_id: str, key: str, session: Session = Depends(db_session),
             user: str = Depends(current_user)) -> Response:
    """Serve an auto-generated highlight clip (mp4) by object key (from an
    event's `payload.clip`). Path-checked to this match's clips."""
    _owned(MatchRepository(session), match_id, user)
    if not key.startswith(frame_prefix(match_id) + "clips/"):
        raise HTTPException(403, "key does not belong to this match")
    try:
        data = get_object_store().get(key)
    except Exception:
        raise HTTPException(404, "clip not found")
    return Response(content=data, media_type="video/mp4")


VIDEO_CHUNK_BYTES = 4 * 1024 * 1024


def parse_byte_range(rng: str, total: int) -> tuple[str, int, int]:
    """Parse a single HTTP byte-range header.

    Returns (kind, start, end) where kind is:
      "ok"            -> serve 206 for the inclusive [start, end]
      "unsatisfiable" -> serve 416 (syntactically valid but outside the file)
      "ignore"        -> header is malformed; RFC 9110 says serve the full 200

    Handles all three forms. The suffix form (`bytes=-500`, meaning the LAST 500
    bytes) is the one that matters most in practice: MP4 players use it to locate
    a trailing `moov` atom, which is exactly how a non-faststart console capture
    is laid out. Mishandling it previously returned the whole file with a bogus
    206 Content-Range - wrong bytes, and a multi-GB read straight into memory.
    """
    m = _re.match(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$", rng or "")
    if not m or total <= 0:
        return ("ignore", 0, 0)
    first, last = m.group(1), m.group(2)
    if not first and not last:
        return ("ignore", 0, 0)

    if not first:
        # Suffix range: the final N bytes.
        n = int(last)
        if n <= 0:
            return ("unsatisfiable", 0, 0)
        start, end = max(0, total - n), total - 1
    else:
        start = int(first)
        if start >= total:
            return ("unsatisfiable", 0, 0)
        end = int(last) if last else total - 1

    end = min(end, total - 1)
    if end < start:
        return ("unsatisfiable", 0, 0)
    # Never serve more than one chunk per request, however much was asked for.
    # A short 206 is legal and keeps peak memory bounded regardless of file size.
    end = min(end, start + VIDEO_CHUNK_BYTES - 1)
    return ("ok", start, end)


@router.get("/{match_id}/video")
def get_video(match_id: str, request: Request, session: Session = Depends(db_session),
              user: str = Depends(current_user)) -> Response:
    """Serve the stored source video with HTTP Range support so the moment viewer
    can seek. Bounded to ~4MB per request; the browser asks for more as it plays."""
    _owned(MatchRepository(session), match_id, user)
    key = source_key(match_id)
    store = get_object_store()
    try:
        total = store.size(key)
    except Exception:
        raise HTTPException(404, "video not found")

    kind, start, end = parse_byte_range(request.headers.get("range") or "", total)
    if kind == "unsatisfiable":
        return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
    if kind == "ok":
        data = store.get_range(key, start, end)
        return Response(
            content=data, status_code=206, media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(data)),
                "Cache-Control": "no-store",
            },
        )

    # No (usable) Range header. Stream in chunks rather than reading the whole
    # object - a match video is routinely multi-GB and `store.get()` would pull
    # all of it into the API process at once.
    def _chunks():
        pos = 0
        while pos < total:
            stop = min(pos + VIDEO_CHUNK_BYTES, total) - 1
            yield store.get_range(key, pos, stop)
            pos = stop + 1

    return StreamingResponse(
        _chunks(), media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Content-Length": str(total)},
    )


@router.get("")
def list_matches(
    game_id: str | None = None,
    edition: str | None = None,
    session: Session = Depends(db_session),
    user: str = Depends(current_user),
) -> JSONResponse:
    matches = MatchRepository(session).list(game_id=game_id, edition=edition, identity=user)
    return JSONResponse([m.model_dump(mode="json") for m in matches])


@router.get("/{match_id}/progress")
async def progress_stream(match_id: str, user: str = Depends(current_user)):
    """SSE stream of pipeline progress. Emits a snapshot immediately, then live
    updates from the worker via Redis pub/sub, and closes on a terminal status."""
    settings = get_settings()

    async def event_gen():
        # Snapshot first, so a late subscriber isn't left hanging.
        from core.storage.db import get_session

        session = get_session()
        try:
            match = MatchRepository(session).get(match_id)
        finally:
            session.close()
        # Same ownership rule as the REST endpoints: another account's progress
        # stream must look identical to a missing match.
        if match is None or ((match.capture or {}).get("identity") not in (None, "", user)):
            yield {"event": "error", "data": json.dumps({"detail": "match not found"})}
            return
        yield {"event": "snapshot", "data": json.dumps({"status": match.status.value})}
        if match.status.value in _TERMINAL:
            yield {"event": "done", "data": json.dumps({"status": match.status.value})}
            return

        r = aioredis.from_url(settings.redis_url)
        pubsub = r.pubsub()
        await pubsub.subscribe(f"match-progress:{match_id}")
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
                if msg is None:
                    yield {"event": "ping", "data": "{}"}
                    continue
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                yield {"event": "progress", "data": data}
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    parsed = {}
                if is_terminal_event(parsed):
                    yield {"event": "done", "data": data}
                    break
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()
            await r.close()

    return EventSourceResponse(event_gen())


@router.get("/patterns/{game_id}/{edition}")
def patterns(game_id: str, edition: str, last: int = 8,
             session: Session = Depends(db_session),
             user: str = Depends(current_user)) -> dict:
    """What keeps going wrong across matches - the cross-match memory the coach
    already uses to spot repeats. It was computed for the prompt and never shown
    to the player; this exposes it.

    Tag ids are game-specific, so the human labels come from the adapter rather
    than being hardcoded here.
    """
    history = MatchRepository(session).recurring_issues(user, limit=max(1, min(50, last)))
    labels = {}
    try:
        for v in registry.get(game_id, edition).issue_vocabulary():
            labels[v.get("tag")] = v.get("label")
    except UnknownGameError:
        pass
    issues = [
        {**i, "label": labels.get(i.get("tag")) or str(i.get("tag", "")).replace("_", " ")}
        for i in (history.get("issues") or [])
    ]
    return {
        "matches": history.get("matches", 0),
        "issues": issues,
        "formation": history.get("formation", ""),
    }


@router.get("/trends/{game_id}/{edition}", response_model=list[TrendResponse])
def trends(game_id: str, edition: str, last: int | None = None,
           session: Session = Depends(db_session),
           user: str = Depends(current_user)) -> list[TrendResponse]:
    matches = MatchRepository(session).list(game_id=game_id, edition=edition, identity=user)
    if last and last > 0:
        # `list` is newest-first; trim to the most recent N so the page can ask
        # "how am I doing lately" rather than only "ever".
        matches = matches[:last]
    out: list[TrendResponse] = []
    for t in build_trends(matches):
        out.append(
            TrendResponse(
                key=t.key,
                label=t.label,
                unit=t.unit,
                higher_is_better=t.higher_is_better,
                latest=t.latest,
                previous=t.previous,
                delta=t.delta,
                improving=t.improving,
                average=t.average,
                points=[{"match_id": p.match_id, "created_at": p.created_at, "value": p.value} for p in t.points],
            )
        )
    return out
