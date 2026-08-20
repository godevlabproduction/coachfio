"""Research the queued knowledge gaps in bulk.

    docker compose run --rm api python -m tools.drain_gaps            # dry run
    docker compose run --rm api python -m tools.drain_gaps --apply    # research
    docker compose run --rm api python -m tools.drain_gaps --apply --limit 20

Why this exists. Gaps are the questions the coach raised while watching real
footage - the highest-signal list of what it does not know about the game. They
are resolved a few per match by the self-learning step, which is fine as a
trickle but hopeless as a backlog: 101 queued at 3 per match is 34 matches.

Each gap costs one Google-grounded Gemini call. The dry run prints what would be
researched and the estimated spend so it is never a surprise.

Answers land in knowledge/learned.yaml with their sources, and every future
report is grounded in them - so this is worth reading afterwards rather than
trusting blindly. `tools.review_knowledge` exists for that.
"""
from __future__ import annotations

import sys

from adapters.ea_fc_26 import knowledge_base as kb
from adapters.ea_fc_26.prompts import fragments
from core.ai.gemini_video import GeminiVideoModel
from core.config import Settings

APPLY = "--apply" in sys.argv
LIMIT = None
if "--limit" in sys.argv:
    LIMIT = int(sys.argv[sys.argv.index("--limit") + 1])

# What one grounded research call costs, from the observed per-match figures.
# Deliberately rough and rounded UP - the point is an order of magnitude before
# spending, not an invoice.
EST_USD_PER_GAP = 0.004


def main() -> None:
    s = Settings()
    if not s.openai_api_key:
        print("OPENAI_API_KEY is empty - nothing to research with.")
        return

    gaps = kb.open_gaps(limit=LIMIT)
    if not gaps:
        print("No open gaps. The queue is clear.")
        return

    print(f"{len(gaps)} open gap(s)"
          + (f" (limited to {LIMIT})" if LIMIT else "")
          + f", about ${len(gaps) * EST_USD_PER_GAP:.2f} to research\n")

    if not APPLY:
        for g in gaps[:15]:
            print(f"  - {g.get('question', '')[:100]}")
        if len(gaps) > 15:
            print(f"  ... and {len(gaps) - 15} more")
        print("\nDry run. Re-run with --apply to research them.")
        return

    model = GeminiVideoModel(
        api_key=s.openai_api_key,
        in_usd_per_mtok=s.openai_input_usd_per_mtok,
        out_usd_per_mtok=s.openai_output_usd_per_mtok,
        timeout=s.gemini_http_timeout_s,
        deadline_s=s.gemini_http_deadline_s,
    )
    template = fragments("home").get("research_query", "{question}")

    learned = unknown = failed = 0
    spent = 0.0
    for i, g in enumerate(gaps, 1):
        q = template.format(question=g.get("question", ""))
        try:
            r = model.research(model=s.gemini_video_model, question=q)
        except Exception as exc:  # noqa: BLE001 - one bad gap must not stop the run
            failed += 1
            print(f"  [{i}/{len(gaps)}] FAILED  {g.get('question', '')[:70]} - {exc}")
            continue
        spent += float(r.get("cost_usd", 0.0) or 0.0)
        ans = (r.get("answer") or "").strip()
        # The prompt tells the model to reply exactly 'unknown' when the thing is
        # not real. Storing that would be worse than storing nothing - it would
        # ground future reports in a non-answer.
        if not ans or ans.lower() == "unknown" or len(ans) <= 15:
            unknown += 1
            print(f"  [{i}/{len(gaps)}] unknown {g.get('question', '')[:70]}")
            continue
        kb.resolve_gap(g["id"], ans, r.get("sources", []))
        learned += 1
        print(f"  [{i}/{len(gaps)}] learned {g.get('question', '')[:70]}")

    print(f"\nlearned {learned}, unknown {unknown}, failed {failed}. "
          f"Spent ${spent:.4f}.")
    print("Answers are in adapters/ea_fc_26/knowledge/learned.yaml - worth a read: "
          "they ground every future report.")


if __name__ == "__main__":
    main()
