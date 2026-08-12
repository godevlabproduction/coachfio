"""One-off, idempotent: add the matches.insights column to an existing DB.
(New/prod DBs get it from the model via create_all; this patches the dev DB.)

    docker compose run --rm api python -m tools._add_insights_col
"""
from sqlalchemy import text

from core.storage.db import get_session, init_db

init_db()
s = get_session()
try:
    s.execute(text("ALTER TABLE matches ADD COLUMN IF NOT EXISTS insights jsonb DEFAULT '[]'::jsonb"))
    s.commit()
    print("insights column ensured")
finally:
    s.close()
