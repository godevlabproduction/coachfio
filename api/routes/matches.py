from __future__ import annotations

import asyncio
import json

import redis.asyncio as aioredis
import re as _re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
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
from core.models.enums import MatchStatus
from core.progress.trends import build_trends
from core.models.enums import SourceType
from core.storage.frame_keys import frame_key, frame_prefix, source_key
from core.storage.objectstore import get_object_store
from core.storage.repository import MatchRepository
from core.storage.usage import get_usage, increment_usage

router = APIRouter(prefix="/api/matches", tags=["matches"])

_TERMINAL = {
    MatchStatus.COMPLETE.value,
    MatchStatus.FAILED.value,
    MatchStatus.OVER_BUDGET.value,
}


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

    # Usage limit by matches analysed (per the brief — not by time).
    limit = get_settings().free_match_limit
    if get_usage(session, user) >= limit:
        raise HTTPException(402, f"match limit reached ({limit}); upgrade to analyse more")

    match = Match(
        game_id=body.game_id,
        game_edition=body.edition,
        source_type=body.source_type,
        # Stamp the identity server-side so the coaching loop can track this player
        # across matches (the "learns you" longitudinal memory).
        capture={**(body.capture or {}), "identity": user},
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
) -> FrameUploadResponse:
    # SYNC endpoint on purpose: FastAPI runs it in the threadpool, so the blocking
    # DB + object-store calls here never freeze the event loop (async handlers
    # doing sync IO stalled the whole API under concurrent uploads).
    repo = MatchRepository(session)
    match = repo.get(match_id)
    if match is None:
        raise HTTPException(404, "match not found")

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
) -> dict:
    """Upload a non-video source (replay file / API export) for a replay/API
    match. The counterpart to per-frame upload on the video path."""
    repo = MatchRepository(session)
    match = repo.get(match_id)
    if match is None:
        raise HTTPException(404, "match not found")
    if match.source_type == SourceType.VIDEO:
        raise HTTPException(400, "this match is a video source; upload frames, not a source file")

    data = await file.read()
    key = source_key(match_id)
    await run_in_threadpool(get_object_store().put, key, data, "application/octet-stream")
    if match.status == MatchStatus.CREATED:
        repo.set_status(match_id, MatchStatus.UPLOADING)
    return {"match_id": match_id, "key": key, "bytes": len(data)}


@router.post("/{match_id}/complete")
def complete_upload(match_id: str, session: Session = Depends(db_session)) -> dict:
    repo = MatchRepository(session)
    match = repo.get(match_id)
    if match is None:
        raise HTTPException(404, "match not found")
    repo.set_status(match_id, MatchStatus.QUEUED)
    session.commit()  # ensure the worker sees QUEUED before it runs

    # Import here to avoid the API importing Celery's task graph at module load.
    from workers.tasks import run_match_pipeline

    run_match_pipeline.delay(match_id)
    return {"match_id": match_id, "status": MatchStatus.QUEUED.value}


@router.get("/{match_id}")
def get_match(match_id: str, session: Session = Depends(db_session)) -> JSONResponse:
    match = MatchRepository(session).get(match_id)
    if match is None:
        raise HTTPException(404, "match not found")
    return JSONResponse(match.model_dump(mode="json"))


@router.get("/{match_id}/frame")
def get_frame(match_id: str, key: str) -> Response:
    """Serve one stored frame JPEG by object key (referenced from an event/insight
    `frame_refs`). Path-checked so it can only read this match's frames."""
    if not key.startswith(frame_prefix(match_id)):
        raise HTTPException(403, "key does not belong to this match")
    try:
        data = get_object_store().get(key)
    except Exception:
        raise HTTPException(404, "frame not found")
    return Response(content=data, media_type="image/jpeg")


@router.get("/{match_id}/clip")
def get_clip(match_id: str, key: str) -> Response:
    """Serve an auto-generated highlight clip (mp4) by object key (from an
    event's `payload.clip`). Path-checked to this match's clips."""
    if not key.startswith(frame_prefix(match_id) + "clips/"):
        raise HTTPException(403, "key does not belong to this match")
    try:
        data = get_object_store().get(key)
    except Exception:
        raise HTTPException(404, "clip not found")
    return Response(content=data, media_type="video/mp4")


@router.get("/{match_id}/video")
def get_video(match_id: str, request: Request) -> Response:
    """Stream the stored source video with HTTP Range support so the moment viewer
    can seek. Only serves ~4MB per request; the browser asks for more as it plays."""
    key = source_key(match_id)
    store = get_object_store()
    try:
        total = store.size(key)
    except Exception:
        raise HTTPException(404, "video not found")

    rng = request.headers.get("range") or request.headers.get("Range")
    if rng:
        m = _re.match(r"bytes=(\d+)-(\d*)", rng)
        start = int(m.group(1)) if m else 0
        end = int(m.group(2)) if (m and m.group(2)) else min(start + 4 * 1024 * 1024 - 1, total - 1)
        end = min(end, total - 1)
        if start >= total:
            raise HTTPException(416, "range not satisfiable")
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
    data = store.get(key)
    return Response(content=data, media_type="video/mp4",
                   headers={"Accept-Ranges": "bytes", "Content-Length": str(total)})


@router.get("")
def list_matches(
    game_id: str | None = None,
    edition: str | None = None,
    session: Session = Depends(db_session),
) -> JSONResponse:
    matches = MatchRepository(session).list(game_id=game_id, edition=edition)
    return JSONResponse([m.model_dump(mode="json") for m in matches])


@router.get("/{match_id}/progress")
async def progress_stream(match_id: str):
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
        if match is None:
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
                if parsed.get("status") in _TERMINAL or (
                    parsed.get("stage") == "pipeline" and parsed.get("status") == "final"
                ):
                    yield {"event": "done", "data": data}
                    break
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()
            await r.close()

    return EventSourceResponse(event_gen())


@router.get("/trends/{game_id}/{edition}", response_model=list[TrendResponse])
def trends(game_id: str, edition: str, session: Session = Depends(db_session)) -> list[TrendResponse]:
    matches = MatchRepository(session).list(game_id=game_id, edition=edition)
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
