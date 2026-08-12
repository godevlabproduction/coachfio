"""Wait for a match to finish, then export its coaching report to a PDF.

    docker compose run --rm api sh -c "pip install -q fpdf2 && \
        python -m tools.wait_and_export <match_id> [out_name] [max_wait_s]"
"""
from __future__ import annotations

import sys
import time

from sqlalchemy import text

from core.storage.db import session_scope

TERMINAL = ("complete", "failed", "over_budget")


def main() -> None:
    mid = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else None
    max_wait = int(sys.argv[3]) if len(sys.argv) > 3 else 600
    deadline = time.time() + max_wait

    status = None
    while time.time() < deadline:
        with session_scope() as s:
            status = s.execute(
                text("select status from matches where id=:m"), {"m": mid}
            ).scalar()
        if status in TERMINAL:
            break
        time.sleep(5)
    print(f"final status: {status}")

    if status != "complete":
        print("not complete — no PDF written.")
        sys.exit(0 if status else 1)

    from tools.export_pdf import _report_for, build
    if name:
        build.out_name = name if name.endswith(".pdf") else name + ".pdf"  # type: ignore[attr-defined]
    row, rep = _report_for(mid)
    if not row:
        print("completed but no coaching_report found.")
        sys.exit(1)
    path = build(row, rep)
    out = row["outcome"] or {}
    print(f"WROTE {path}  ({path.stat().st_size} bytes) | score {out.get('score')} {out.get('result')}")


if __name__ == "__main__":
    main()
