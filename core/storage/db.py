"""Database engine + session. Schema is owned by Alembic (migrations/ at the
repo root): init_db() upgrades to head on boot, so "add a column" is a
migration file, not a silent create_all no-op followed by a hand-written
tools/_add_*.py patch (how it worked before, and how five of those scripts
came to exist)."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import get_settings

log = logging.getLogger("coachio.db")

# The revision that matches what create_all used to build. Pre-Alembic databases
# are stamped here so history starts without replaying DDL they already have.
_BASELINE_REV = "0001"


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _init_engine() -> None:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def _alembic_config():
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]  # core/storage/db.py -> repo root
    cfg = Config(str(root / "alembic.ini"))
    # Absolute, so migrations resolve no matter the process's working directory
    # (uvicorn runs from the repo root; tools and Celery may not).
    cfg.set_main_option("script_location", str(root / "migrations"))
    return cfg


def init_db() -> None:
    """Bring the schema to the current head.

    Three cases, decided by inspecting the database:
    - Fresh DB (no tables): run every migration from the baseline.
    - Pre-Alembic DB (app tables but no alembic_version): create_all once to
      fill in any table it is missing, stamp the baseline, then upgrade - its
      schema IS the baseline; replaying the baseline DDL would fail on the
      first CREATE TABLE.
    - Already versioned: plain upgrade to head (usually a no-op).
    """
    _init_engine()
    from alembic import command

    from core.storage import models  # noqa: F401  (registers tables on Base.metadata)

    cfg = _alembic_config()
    names = set(inspect(_engine).get_table_names())
    if "matches" in names and "alembic_version" not in names:
        log.info("pre-Alembic database detected - stamping baseline %s", _BASELINE_REV)
        Base.metadata.create_all(_engine)  # tables only; never alters columns
        command.stamp(cfg, _BASELINE_REV)
    command.upgrade(cfg, "head")


def get_session() -> Session:
    _init_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
