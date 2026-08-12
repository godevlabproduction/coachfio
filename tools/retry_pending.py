"""Auto-retry pending native-video matches until they produce a coaching report
(rides out a Gemini video 503 outage), exporting each PDF as soon as it succeeds.

    docker compose run --rm api sh -c "pip install -q fpdf2 && \
        python -m tools.retry_pending <id> [id ...] [--max-min 60]"
"""
from __future__ import annotations

import sys
import time

from sqlalchemy import text

from core.storage.db import session_scope
from workers.tasks import run_match_pipeline


def has_report(mid: str) -> bool:
    with session_scope() as s:
        ins = s.execute(text("select insights from matches where id=:m"), {"m": mid}).scalar()
    return any(x.get("kind") == "coaching_report" for x in (ins or []))


def export(mid: str) -> None:
    from tools.export_pdf import _report_for, build
    build.out_name = f"report-{mid[:8]}.pdf"  # type: ignore[attr-defined]
    row, rep = _report_for(mid)
    if row:
        p = build(row, rep)
        out = row["outcome"] or {}
        print(f"WROTE {p} | score {out.get('score')} {out.get('result')}", flush=True)


def main() -> None:
    args = sys.argv[1:]
    max_min = 60
    if "--max-min" in args:
        i = args.index("--max-min")
        max_min = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    ids = args
    deadline = time.time() + max_min * 60
    pending = list(ids)
    rnd = 0
    while pending and time.time() < deadline:
        rnd += 1
        still = []
        for mid in pending:
            if has_report(mid):
                export(mid)
                continue
            try:
                run_match_pipeline(mid)
            except Exception as e:  # noqa: BLE001
                print(f"round {rnd} {mid[:8]}: error {e}", flush=True)
            if has_report(mid):
                print(f"round {rnd} {mid[:8]}: SUCCESS", flush=True)
                export(mid)
            else:
                still.append(mid)
        pending = still
        if pending:
            print(f"round {rnd}: still pending {[m[:8] for m in pending]} (Gemini video still down?) — sleeping 120s",
                  flush=True)
            time.sleep(120)
    print(f"DONE. remaining unprocessed: {[m[:8] for m in pending]}", flush=True)


if __name__ == "__main__":
    main()
