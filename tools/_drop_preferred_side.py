"""Drop users.preferred_side.

Side is a per-match fact, not a preference: it decides which goals count as
conceded and whether a report says you won. A remembered value is a wrong answer
waiting to be applied to the next upload, so it is chosen at upload and nowhere
else.

DROP COLUMN removes the field from the table. Every user row survives; only that
one value goes. Idempotent, so it is safe to run more than once.
"""
from __future__ import annotations

from sqlalchemy import text

import core.storage.db as db


def main() -> None:
    # init_db() is what creates the engine, so the module attribute has to be read
    # AFTER it runs. Importing `_engine` at module level captures None.
    db.init_db()
    with db._engine.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM users")).scalar()
        conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS preferred_side"))
        after = conn.execute(text("SELECT count(*) FROM users")).scalar()
        print(f"users before={before} after={after} (rows must be unchanged)")
        assert before == after, "row count changed - aborting"
    print("preferred_side dropped")


if __name__ == "__main__":
    main()
