"""Drain the FC 26 knowledge-gap queue: research every OPEN gap via Gemini
Google-Search grounding and file sourced facts into knowledge/learned.yaml.

    docker compose run --rm api python -m tools.learn [max]

Shows what it learned. Review knowledge/learned.yaml (entries are flagged
reviewed: false) and correct anything off.
"""
from __future__ import annotations

import sys

from adapters.ea_fc_26 import knowledge_base as kb
from core.ai.gemini_video import GeminiVideoModel
from core.config import get_settings


def main() -> None:
    s = get_settings()
    if not s.openai_api_key:
        print("No Gemini key (OPENAI_API_KEY) set.")
        sys.exit(1)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    gaps = kb.open_gaps(limit=limit)
    if not gaps:
        print("No open knowledge gaps. The brain is up to date.")
        return
    model = GeminiVideoModel(
        api_key=s.openai_api_key,
        in_usd_per_mtok=s.openai_input_usd_per_mtok,
        out_usd_per_mtok=s.openai_output_usd_per_mtok,
    )
    print(f"researching {len(gaps)} open gap(s) on {s.gemini_video_model} ...")
    learned, cost = 0, 0.0
    for g in gaps:
        q = (f"In EA Sports FC 26 (current title update), {g['question']} "
             f"Answer in 1-3 concise factual sentences; if it is not specifically a real "
             f"FC 26 thing, reply exactly 'unknown'.")
        r = model.research(model=s.gemini_video_model, question=q)
        cost += r.get("cost_usd", 0.0)
        ans = (r.get("answer") or "").strip()
        if ans and ans.lower() != "unknown" and len(ans) > 15:
            kb.resolve_gap(g["id"], ans, r.get("sources", []))
            learned += 1
            print(f"  [+] {g['question']}\n      -> {ans[:160]}")
        else:
            print(f"  [-] {g['question']}  (no confident answer)")
    print(f"\nlearned {learned}/{len(gaps)} facts, cost ${cost:.4f}. "
          f"Review adapters/ea_fc_26/knowledge/learned.yaml, then restart worker.")


if __name__ == "__main__":
    main()
