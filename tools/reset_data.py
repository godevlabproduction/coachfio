"""Wipe all analysed matches (and their metrics/events via FK cascade).

Dev/test convenience — clears the trends view without dropping the schema:

    docker compose run --rm api python -m tools.reset_data
"""
from sqlalchemy import delete, text

from core.storage.db import get_session, init_db
from core.storage.models import MatchRow


def main() -> None:
    init_db()  # ensure tables exist
    session = get_session()
    try:
        session.execute(text("SET lock_timeout='8s'"))
        n = session.execute(delete(MatchRow)).rowcount
        session.commit()
        print(f"deleted matches: {n}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
