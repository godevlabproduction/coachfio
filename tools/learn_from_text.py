"""Teach the FC 26 brain from a TEXT notes file (already-distilled knowledge).

The video learner has Gemini distill a clip; this is the manual counterpart for
when you already have clean notes (a pro's write-up, meta breakdown, your own
observations). Point it at a YAML file and each fact lands in the knowledge brain
(`knowledge/learned.yaml`), grounding every future coaching report.

YAML shape:
    source: text:meta-notes-v1
    facts:
      - title: Finesse Shots
        detail: The strongest finishing mechanic ...
        controls: R1 + shoot / RB + shoot        # optional
        category: attacking                       # optional
        tags: [shooting, finesse, meta]           # optional

    docker compose run --rm api python -m tools.learn_from_text tools/notes_meta_v1.yaml

Restart the coach afterwards: docker compose restart worker api
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

from adapters.ea_fc_26 import knowledge_base as kb


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m tools.learn_from_text <notes.yaml>")
        return
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"notes file not found: {path}")
        return
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    source = str(doc.get("source") or f"text:{path.stem}")
    facts = doc.get("facts") or []

    added = 0
    for f in facts:
        title = str(f.get("title") or "").strip()
        detail = str(f.get("detail") or "").strip()
        controls = str(f.get("controls") or "").strip()
        if controls:
            detail = f"{detail} Controls: {controls}"
        cat = str(f.get("category") or "").strip()
        tags = [str(t) for t in (f.get("tags") or [])]
        if cat:
            tags.append(cat)
        if kb.add_learned(title, detail, sources=[source], tags=tags):
            added += 1
            print(f"  + {title}")

    print(f"\nLEARNED {added}/{len(facts)} facts from {path.name} (source={source}).")
    print("Restart the coach to use them: docker compose restart worker api")


if __name__ == "__main__":
    main()
