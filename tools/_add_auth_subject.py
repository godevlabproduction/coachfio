"""One-off, idempotent: the auth_subject column on users.

    docker compose run --rm api python -m tools._add_auth_subject

Why this column exists, because it is the part that is easy to get wrong:

`users.user_id` is our primary key AND it is written into every match as
`capture->>'identity'`. If a hosted auth provider's subject claim were used as
that id, the match history would be married to that vendor - changing provider
would mean rewriting JSONB across every match row, and getting it wrong would
orphan somebody's reports.

So the provider's subject lives in its own column and maps to the id we own.
Adopting, or later changing, a provider re-populates one column and touches no
match data. Nullable because every existing account predates it; unique because
two accounts must never resolve to the same login.
"""
from sqlalchemy import text

from core.storage.db import get_session, init_db

init_db()
s = get_session()
try:
    s.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_subject varchar(255)"))
    # Partial index. Postgres allows many NULLs under a plain UNIQUE constraint,
    # so uniqueness is not the reason - the reason is that every row is NULL
    # until a provider is connected, and there is no point indexing all of them.
    s.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_auth_subject "
                   "ON users (auth_subject) WHERE auth_subject IS NOT NULL"))
    s.commit()
    n = s.execute(text("SELECT count(*) FROM users")).scalar_one()
    linked = s.execute(
        text("SELECT count(*) FROM users WHERE auth_subject IS NOT NULL")).scalar_one()
    print(f"ok - users={n}, linked to a provider={linked} (expected 0 until one is connected)")
finally:
    s.close()
