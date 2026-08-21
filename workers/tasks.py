from __future__ import annotations

import logging

from celery.exceptions import SoftTimeLimitExceeded

from adapters.base.registry import load_builtin_adapters, registry
from core.ai.vision import build_vision
from core.config import get_settings
from core.extraction.frames import Frame, load_frame_image
from core.extraction.ocr import get_ocr_engine
from core.models.enums import MatchStatus, SourceType
from core.pipeline.context import PipelineContext
from core.pipeline.cost import CostAccountant
from core.pipeline.runner import run_pipeline
from core.storage.db import session_scope
from core.storage.frame_keys import frame_prefix, parse_frame_key, source_key
from core.storage.objectstore import get_object_store
from core.storage.repository import MatchRepository
from workers.celery_app import celery_app
from workers.progress import RedisProgressReporter

log = logging.getLogger("coachio.worker")


def _load_frames(match_id: str) -> list[Frame]:
    store = get_object_store()
    frames: list[Frame] = []
    for key in store.list(frame_prefix(match_id)):
        parsed = parse_frame_key(key)
        if parsed is None:
            continue
        index, ts = parsed
        image = load_frame_image(store.get(key))
        frames.append(Frame(index=index, timestamp_ms=ts, key=key, image=image))
    frames.sort(key=lambda f: f.index)
    return frames


@celery_app.task(name="workers.run_match_pipeline")
def run_match_pipeline(match_id: str) -> dict:
    load_builtin_adapters()
    settings = get_settings()
    reporter = RedisProgressReporter(match_id)

    with session_scope() as session:
        repo = MatchRepository(session)
        match = repo.get(match_id)
        if match is None:
            reporter.report("pipeline", "failed", f"match {match_id} not found")
            return {"match_id": match_id, "status": "not_found"}
        repo.set_status(match_id, MatchStatus.PROCESSING)

    # VIDEO -> load frames (OCR pipeline); VIDEO_NATIVE/replay/API -> load the raw
    # source object (whole video for Gemini, or a replay/API export).
    frames: list[Frame] = []
    source_bytes: bytes | None = None
    if match.source_type == SourceType.VIDEO:
        reporter.report("pipeline", "loading", "loading frames from object store")
        frames = _load_frames(match_id)
        if not frames:
            with session_scope() as session:
                MatchRepository(session).set_status(match_id, MatchStatus.FAILED)
            reporter.report("pipeline", "failed", "no frames uploaded")
            return {"match_id": match_id, "status": "no_frames"}
    else:
        reporter.report("pipeline", "loading", "loading uploaded source (video/replay)")
        try:
            source_bytes = get_object_store().get(source_key(match_id))
        except Exception:
            with session_scope() as session:
                MatchRepository(session).set_status(match_id, MatchStatus.FAILED)
            reporter.report("pipeline", "failed", "no source uploaded")
            return {"match_id": match_id, "status": "no_source"}

    # Longitudinal memory: this player's recurring issues across past matches.
    identity = (match.capture or {}).get("identity", "")
    player_history = None
    if identity:
        with session_scope() as session:
            player_history = MatchRepository(session).recurring_issues(identity)
            # What the player said was WRONG with recent reports. Loaded here
            # with the rest of their memory so the coach is told what to fix
            # before it writes, rather than repeating it and being told again.
            from core.storage import report_feedback as _rf
            player_history["complaints"] = _rf.recent_complaints(session, identity)

    duration_ms = max((f.timestamp_ms for f in frames), default=0)
    adapter = registry.get(match.game_id, match.game_edition)
    ident = adapter.identity()
    match.adapter_version = f"{ident.game_id}@{ident.edition}"

    ctx = PipelineContext(
        match=match,
        adapter=adapter,
        frames=frames,
        ocr=get_ocr_engine(settings.ocr_engine),
        settings=settings,
        cost=CostAccountant.for_match(settings.match_budget_usd, duration_ms),
        reporter=reporter,
        vision=build_vision(settings),
        object_store=get_object_store(),
        source_bytes=source_bytes,
        player_history=player_history,
    )

    # Native whole-video path runs a single Gemini stage; everything else runs
    # the default frame/replay pipeline.
    from core.pipeline.stages import VIDEO_NATIVE_STAGES
    stages = VIDEO_NATIVE_STAGES if match.source_type == SourceType.VIDEO_NATIVE else None

    try:
        run_pipeline(ctx, stages=stages)
    except SoftTimeLimitExceeded:
        # The runner's generic handler already set FAILED, but its warning reads
        # "pipeline error: SoftTimeLimitExceeded()" - say what actually happened
        # and what to do about it. Swallowed (not re-raised) so the "final"
        # report below still closes the progress stream before the hard limit.
        match.status = MatchStatus.FAILED
        match.warnings.append(
            "analysis hit the 40-minute time limit and was stopped - "
            "try again; if it keeps happening, trim the video before uploading")
    finally:
        with session_scope() as session:
            MatchRepository(session).save(match)

    # Storage-cost swap: after a successful whole-video run, the stored original
    # (hundreds of MB to GBs) is replaced by the 720p re-encode the stage already
    # produced for its upload. Success only - a failed run keeps the original so
    # a retry starts from full quality. Never fails the match: worst case we
    # keep paying for the big file.
    if (settings.store_compressed_source
            and match.status == MatchStatus.COMPLETE
            and match.source_type == SourceType.VIDEO_NATIVE
            and ctx.compressed_source and source_bytes
            and len(ctx.compressed_source) < len(source_bytes)):
        try:
            get_object_store().put(
                source_key(match_id), ctx.compressed_source, content_type="video/mp4")
            log.info("match %s: stored video shrunk %.1f MB -> %.1f MB", match_id,
                     len(source_bytes) / 1e6, len(ctx.compressed_source) / 1e6)
        except Exception as exc:  # noqa: BLE001 - keeping the original is a safe outcome
            log.warning("match %s: could not swap in compressed video: %s", match_id, exc)

    reporter.report(
        "pipeline",
        "final",
        f"status={match.status.value} cost=${match.cost_usd:.4f} "
        f"confidence={match.parse_confidence}",
        match_status=match.status.value,
    )
    return {"match_id": match_id, "status": match.status.value, "cost_usd": match.cost_usd}
