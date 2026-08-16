"""Targeted cleanup of matches and test accounts.

`tools.reset_data` wipes every match, which is the wrong instrument when the goal
is to remove four duplicates and keep seven real analyses. This deletes exactly
what it is given and nothing else.

Two safety properties, because this is not reversible:

  1. DRY RUN BY DEFAULT. It prints what it would delete and changes nothing until
     --apply is passed.
  2. A PROTECTED IDENTITY CANNOT BE DELETED. The owner's account id is refused
     outright, so a mistyped argument cannot take the real account with it.

Object storage is cleaned too. Every artefact of a match lives under
matches/<id>/ (source video, extracted frames, generated clips), so deleting the
row without the prefix leaves the video paid for and unreachable forever.

    docker compose run --rm api python -m tools.cleanup_data            # dry run
    docker compose run --rm api python -m tools.cleanup_data --apply
"""
from __future__ import annotations

import sys

from sqlalchemy import delete, select, text

import core.storage.db as db
from core.storage.models import MatchRow, UserRow

# The owner. Never deletable by this script, whatever it is asked to do.
PROTECTED_IDENTITY = "fa202b58ccf141b7998ac7add9b95a6e"

# Matches to remove, with the reason recorded so this file explains itself later.
MATCHES: dict[str, str] = {
    # Run 1 (done): 4fb78678, 5df1fd47 (duplicates of 7a992710 carrying the wrong
    # pre-fix 2-5), 80933a10, c5b07451 (duplicate uploads), fdd6e962 (failed).
}

# Accounts created for testing. All example.com; any real address stays.
USERS: dict[str, str] = {
    # Run 1 (done): 7 example.com test accounts + the preview- placeholder identity.
    "67c7f805ede94ebfbc166df57bc00be6": "yfuy (ilijaatanasov00@gmail.com), owns nothing",
}


def _resolve(session, short: str) -> str | None:
    row = session.execute(
        select(MatchRow.id).where(MatchRow.id.like(f"{short}%"))
    ).scalars().all()
    if len(row) != 1:
        return None
    return row[0]


def main() -> None:
    apply = "--apply" in sys.argv
    db.init_db()
    store = None
    try:
        from core.storage.objectstore import get_object_store
        store = get_object_store()
    except Exception as exc:  # noqa: BLE001
        print(f"! object store unavailable ({exc}); DB rows only")

    session = db.get_session()
    try:
        session.execute(text("SET lock_timeout='8s'"))

        # --- matches ---
        match_ids: list[tuple[str, str]] = []
        for short, why in MATCHES.items():
            full = _resolve(session, short)
            if full is None:
                print(f"  match {short}: not found or ambiguous, SKIPPED")
                continue
            owner = session.get(MatchRow, full).capture.get("identity")
            match_ids.append((full, why))
            print(f"  match {short}  owner={str(owner)[:8]}  {why}")

        # --- users (and everything they own) ---
        user_ids: list[tuple[str, str]] = []
        for uid, why in USERS.items():
            if uid == PROTECTED_IDENTITY:
                print(f"  REFUSED: {uid} is the protected identity")
                continue
            if session.get(UserRow, uid) is None:
                print(f"  user {uid[:8]}: not found, SKIPPED")
                continue
            owned = session.execute(
                select(MatchRow.id).where(MatchRow.capture["identity"].astext == uid)
            ).scalars().all()
            user_ids.append((uid, why))
            print(f"  user  {uid[:8]}  {why}  (+{len(owned)} matches)")
            for m in owned:
                match_ids.append((m, f"owned by deleted test account {uid[:8]}"))

        if not apply:
            print(f"\nDRY RUN: would delete {len(match_ids)} matches, {len(user_ids)} users."
                  f"\nRe-run with --apply to commit.")
            return

        before_u = session.execute(text("SELECT count(*) FROM users")).scalar()
        before_m = session.execute(text("SELECT count(*) FROM matches")).scalar()

        for mid, _ in match_ids:
            session.execute(delete(MatchRow).where(MatchRow.id == mid))
        for uid, _ in user_ids:
            session.execute(text("DELETE FROM coach_links WHERE coach_id=:u OR player_id=:u"),
                            {"u": uid})
            session.execute(text("DELETE FROM messages WHERE sender=:u OR recipient=:u"),
                            {"u": uid})
            session.execute(delete(UserRow).where(UserRow.user_id == uid))
        session.commit()

        # Storage AFTER the commit. If this half fails we are left with unreferenced
        # blobs, which is a sweep job; doing it first and failing the commit leaves
        # matches whose video is gone, which is a broken report.
        if store is not None:
            for mid, _ in match_ids:
                try:
                    n = store.delete_prefix(f"matches/{mid}/")
                    if n:
                        print(f"  storage matches/{mid[:8]}/: {n} objects")
                except Exception as exc:  # noqa: BLE001
                    print(f"  storage matches/{mid[:8]}/: FAILED {exc} (orphaned, sweep later)")

        after_u = session.execute(text("SELECT count(*) FROM users")).scalar()
        after_m = session.execute(text("SELECT count(*) FROM matches")).scalar()
        print(f"\nusers   {before_u} -> {after_u}")
        print(f"matches {before_m} -> {after_m}")

        owner = session.get(UserRow, PROTECTED_IDENTITY)
        print(f"protected account intact: {owner is not None and owner.email}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
