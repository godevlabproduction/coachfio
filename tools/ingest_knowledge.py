"""Grow the FC 26 knowledge base from a source URL.

Fetches a page, has the model DISTILL it into structured, paraphrased knowledge
entries (facts + controls, NOT a verbatim copy), and appends them to the right
knowledge YAML with the source link. This is how the 'brain' scales to cover all
the docs/updates/tactics: point it at trusted sources over time.

    docker compose run --rm api python -m tools.ingest_knowledge <url> [target]

target = mechanics | tactics | mistake_remedies | patch_notes  (default: mechanics)

Uses the configured Gemini key (openai engine). Review the appended entries - the
model can misread; the YAML is meant to be human-audited.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

import yaml

from core.ai.vision import build_vision
from core.config import get_settings

_KDIR = Path(__file__).resolve().parents[1] / "adapters" / "ea_fc_26" / "knowledge"

_SCHEMAS = {
    "mechanics": {
        "keys": "id, area, title, detail, controls (PS/Xbox, optional), tags[]",
        "note": "Concrete gameplay mechanics/controls a coach can cite.",
    },
    "tactics": {
        "keys": "id, area, title, detail, tags[]",
        "note": "FC IQ roles, focuses, formations, set pieces, mentality.",
    },
    "mistake_remedies": {
        "keys": "id, problem, fix (with controls), drill, match[] (trigger phrases), tags[]",
        "note": "A player mistake mapped to an FC-26-accurate fix + a concrete drill.",
    },
    "patch_notes": {
        "keys": "version, date, summary, tags[]",
        "note": "One entry per title update: what changed for gameplay.",
    },
}


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "coachio-knowledge/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    html = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text[:24000]


def _distill(text: str, target: str, url: str) -> list[dict]:
    s = get_settings()
    vm = build_vision(s)
    spec = _SCHEMAS[target]
    schema = {
        "type": "object",
        "properties": {"entries": {"type": "array", "items": {"type": "object"}}},
        "required": ["entries"],
    }
    prompt = (
        f"You are building a factual EA Sports FC 26 knowledge base. From the source "
        f"text below, extract 5-15 SPECIFIC, factual '{target}' entries. {spec['note']}\n"
        f"Each entry must have keys: {spec['keys']}. PARAPHRASE into short facts (no "
        f"verbatim copying). Only include things clearly stated for FC 26. Return JSON "
        f"{{\"entries\": [...]}}.\n\nSOURCE ({url}):\n{text}"
    )
    res = vm.generate(model=s.stage2_model, prompt=prompt, images_jpeg=[],
                      schema=schema, max_tokens=4000)
    entries = res.data.get("entries") or []
    for e in entries:
        e.setdefault("source", url)
    print(f"  distilled {len(entries)} entries (${res.cost_usd:.4f})")
    return entries


def _append(target: str, new: list[dict]) -> int:
    path = _KDIR / f"{target}.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc = doc or {}
    existing = doc.get("entries") or []
    seen = {(e.get("id") or e.get("title") or e.get("version") or "").lower() for e in existing}
    added = 0
    for e in new:
        key = (e.get("id") or e.get("title") or e.get("version") or "").lower()
        if key and key in seen:
            continue
        existing.append(e)
        seen.add(key)
        added += 1
    doc["entries"] = existing
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return added


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    url = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else "mechanics"
    if target not in _SCHEMAS:
        print(f"target must be one of: {', '.join(_SCHEMAS)}")
        sys.exit(1)
    print(f"fetching {url} ...")
    text = _fetch_text(url)
    print(f"  {len(text)} chars; distilling into '{target}' ...")
    entries = _distill(text, target, url)
    added = _append(target, entries)
    print(f"ADDED {added} new entries to {target}.yaml (review them!). "
          f"Restart worker to use: docker compose restart worker")
    print(json.dumps(entries[:2], indent=2)[:800])


if __name__ == "__main__":
    main()
