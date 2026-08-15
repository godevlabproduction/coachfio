"""One-off, idempotent: role/checklist columns on users + set the founding
account's role. New TABLES (coach_links, messages) come from create_all via
init_db; ALTERs are needed only for columns on existing tables.

    docker compose run --rm api python -m tools._migrate_roles
"""
from sqlalchemy import text

from core.storage.db import get_session, init_db

# Explicit id, not an email lookup - this must never touch any other row.
FOUNDER_ID = "fa202b58ccf141b7998ac7add9b95a6e"

init_db()  # creates coach_links + messages if missing
s = get_session()
try:
    s.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS role varchar(16) DEFAULT 'player'"))
    s.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS checklist jsonb DEFAULT '{}'::jsonb"))
    s.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar varchar(200) DEFAULT ''"))
    s.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                   "coach_profile jsonb DEFAULT '{}'::jsonb"))
    s.execute(text("ALTER TABLE coach_links ADD COLUMN IF NOT EXISTS "
                   "share_reports boolean DEFAULT false"))
    # Links created before the request/accept flow existed were implicitly
    # accepted - both sides are already chatting. Backfilling them as 'pending'
    # would silently cut working relationships.
    #
    # Added WITHOUT a default on purpose: Postgres backfills a DEFAULT into
    # existing rows, which stamped the live link 'pending' and cut it. Nullable
    # first -> backfill the NULLs -> then set the default for new rows.
    s.execute(text("ALTER TABLE coach_links ADD COLUMN IF NOT EXISTS status varchar(16)"))
    n = s.execute(text("UPDATE coach_links SET status='accepted' WHERE status IS NULL")).rowcount
    s.execute(text("ALTER TABLE coach_links ALTER COLUMN status SET DEFAULT 'pending'"))
    print(f"backfilled {n} pre-existing link(s) as accepted")
    r = s.execute(text("UPDATE users SET role='player' WHERE user_id=:i RETURNING email"),
                  {"i": FOUNDER_ID}).first()
    s.commit()
    print("columns ensured; founder:", r[0] if r else "row not found (will default to player)")
finally:
    s.close()
