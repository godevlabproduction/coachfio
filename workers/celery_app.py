from __future__ import annotations

from celery import Celery

from core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "coachio",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["workers.tasks"],
)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_prefetch_multiplier=1,
    # A match analysis must END. Without a ceiling, one hung run (a stuck
    # ffmpeg, a provider outage that outlasts every retry) pins a worker slot
    # forever. Soft raises SoftTimeLimitExceeded inside the task so it can mark
    # the match failed and say why; hard (5 min later) kills the process if
    # even that never returned. Both far above any legitimate run: deep mode on
    # a full match is ~10-15 min.
    task_soft_time_limit=40 * 60,
    task_time_limit=45 * 60,
)
