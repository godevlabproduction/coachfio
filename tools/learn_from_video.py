"""Teach the FC 26 brain from a TRAINING/TUTORIAL video.

Gemini watches the whole clip once and extracts every teachable FC 26 concept
(mechanics, skill moves, defending/attacking technique, tactics, tips + the exact
controls). Each fact is distilled and written into the adapter's knowledge brain
(`knowledge/learned.yaml`), so every future coaching report can cite it.

    docker compose run --rm -v "C:/Users/micev/Downloads:/dl:ro" api \
        python -m tools.learn_from_video /dl/training1.mp4

One Gemini call (cheap). Restart the worker afterwards so the running coach picks
up the new knowledge: `docker compose restart worker api`.
"""
from __future__ import annotations

import sys
from pathlib import Path

from adapters.ea_fc_26 import knowledge_base as kb
from core.ai.gemini_video import GeminiVideoModel
from core.config import get_settings
from core.pipeline.stages import _compress_video

_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},       # short concept name
                    "detail": {"type": "string"},       # self-contained explanation
                    "controls": {"type": "string"},     # exact button/stick inputs, if any
                    "category": {"type": "string"},     # mechanic|skill_move|defending|attacking|tactic|meta|tip
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "detail"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["facts"],
}

_PROMPT = (
    "You are building the knowledge base for an EA Sports FC 26 AI coach. Watch this "
    "ENTIRE training/tutorial video and EXTRACT EVERYTHING teachable in it: mechanics, "
    "skill moves, dribbling, passing, shooting, defending, positioning, set pieces, "
    "tactics/FC IQ roles, meta tips, and timing. Be EXHAUSTIVE — capture every distinct "
    "concept the video demonstrates or explains.\n"
    "For EACH concept return one 'facts' entry: a short 'title'; a 'detail' that is a "
    "SELF-CONTAINED, factual explanation a coach could quote (what it is, when to use it, "
    "why it works); the EXACT FC 26 'controls' (buttons/sticks, PlayStation + Xbox) if the "
    "video shows an input; a 'category' (mechanic|skill_move|defending|attacking|tactic|"
    "meta|tip); and a few 'tags'. Only include what the video actually teaches — do NOT "
    "invent controls or mechanics. Also give a one-line 'summary' of what the video covers."
)


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m tools.learn_from_video <path-to-video>")
        return
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"video not found: {path}")
        return

    s = get_settings()
    if not s.openai_api_key:
        print("No Gemini key (OPENAI_API_KEY) configured — cannot watch the video.")
        return

    print(f"reading {path.name} ...")
    raw = path.read_bytes()
    print("compressing for a fast upload ...")
    video = _compress_video(raw)

    model = GeminiVideoModel(
        api_key=s.openai_api_key,
        in_usd_per_mtok=s.openai_input_usd_per_mtok,
        out_usd_per_mtok=s.openai_output_usd_per_mtok,
    )
    print(f"watching on {s.gemini_video_model} (one call) ...")
    res = model.analyze(
        model=s.gemini_video_model, prompt=_PROMPT, video=video, schema=_SCHEMA,
        media_resolution=s.gemini_video_media_res, max_tokens=16000,
        on_step=lambda st, d: print(f"  [{st}] {d}"),
    )
    data = res.data or {}
    facts = data.get("facts") or []
    src_tag = "video:" + path.name

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
        if kb.add_learned(title, detail, sources=[src_tag], tags=tags):
            added += 1
            print(f"  + {title}")

    print(f"\nSUMMARY: {data.get('summary', '')}")
    print(f"LEARNED {added}/{len(facts)} facts from {path.name} into the brain. "
          f"cost=${res.cost_usd:.4f}")
    print("Restart the coach to use them: docker compose restart worker api")


if __name__ == "__main__":
    main()
