"""FC 26 knowledge base (Layer 1 'brain'): loads the distilled YAML knowledge and
builds a compact, RELEVANT playbook string to ground the coaching model on real,
current FC 26 mechanics/tactics/remedies — instead of generic football advice.

Game-specific by design: this lives in the adapter, never in core.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

import yaml

_KDIR = Path(__file__).parent / "knowledge"
_GAPS = _KDIR / "_gaps.yaml"          # queue of things to learn
_LEARNED = _KDIR / "learned.yaml"     # auto-researched, sourced facts


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60]


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def _write_yaml(path: Path, doc: dict) -> None:
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")


def queue_gaps(questions: list[str], match_id: str = "") -> int:
    """Add newly-seen knowledge gaps to the queue (deduped by slug)."""
    doc = _read_yaml(_GAPS)
    items = doc.get("gaps") or []
    have = {g.get("id") for g in items}
    added = 0
    for q in questions:
        q = (q or "").strip()
        if not q:
            continue
        gid = _slug(q)
        if not gid or gid in have:
            continue
        items.append({"id": gid, "question": q, "status": "open", "match_id": match_id})
        have.add(gid)
        added += 1
    if added:
        doc["gaps"] = items
        _write_yaml(_GAPS, doc)
    return added


def open_gaps(limit: int | None = None) -> list[dict]:
    items = [g for g in (_read_yaml(_GAPS).get("gaps") or []) if g.get("status") == "open"]
    return items[:limit] if limit else items


def resolve_gap(gap_id: str, answer: str, sources: list[str], tags: list[str] | None = None) -> None:
    """Mark a gap resolved and store the sourced fact in learned.yaml."""
    gdoc = _read_yaml(_GAPS)
    for g in gdoc.get("gaps") or []:
        if g.get("id") == gap_id:
            g["status"] = "resolved"
    _write_yaml(_GAPS, gdoc)

    ldoc = _read_yaml(_LEARNED)
    entries = ldoc.get("entries") or []
    entry = {"id": gap_id, "detail": answer, "sources": sources or [],
             "tags": tags or [], "reviewed": False}
    for i, e in enumerate(entries):
        if e.get("id") == gap_id:
            # Re-learning replaces a thinner/unsourced answer with a better one.
            entries[i] = entry
            break
    else:
        entries.append(entry)
    ldoc["entries"] = entries
    _write_yaml(_LEARNED, ldoc)
    refresh_cache()


def add_learned(title: str, detail: str, sources: list[str] | None = None,
                tags: list[str] | None = None) -> bool:
    """Add (or replace) a distilled fact in learned.yaml — the growth file that
    feeds the coaching playbook. Used to teach the brain from sources other than a
    queued gap (e.g. watching a training video). Deduped by slug(title)."""
    detail = " ".join((detail or "").split())
    lid = _slug(title)
    if not lid or not detail:
        return False
    ldoc = _read_yaml(_LEARNED)
    entries = ldoc.get("entries") or []
    entry = {"id": lid, "detail": detail, "sources": sources or [],
             "tags": tags or [], "reviewed": False}
    for i, e in enumerate(entries):
        if e.get("id") == lid:
            entries[i] = entry
            break
    else:
        entries.append(entry)
    ldoc["entries"] = entries
    _write_yaml(_LEARNED, ldoc)
    refresh_cache()
    return True


@functools.lru_cache(maxsize=1)
def _load() -> dict:
    data: dict = {}
    if not _KDIR.exists():
        return data
    for f in sorted(_KDIR.glob("*.yaml")):
        try:
            data[f.stem] = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - a bad file shouldn't break coaching
            data[f.stem] = {}
    return data


def _select_remedies(remedies: list[dict], hints: str, n: int) -> list[dict]:
    """Pick the remedies most relevant to the detected observations (keyword
    overlap on `match` phrases + tags). Falls back to the first few."""
    if not hints:
        return remedies[:n]
    h = hints.lower()
    scored = []
    for e in remedies:
        score = sum(1 for m in e.get("match", []) if m.lower() in h)
        score += sum(1 for t in e.get("tags", []) if str(t).lower() in h)
        scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    picked = [e for s, e in scored if s > 0][:n]
    return picked or remedies[:n]


def build_playbook(hints: str = "", max_remedies: int = 6) -> str:
    """A compact FC-26 grounding block for the coaching prompt. `hints` is any text
    (e.g. the observation log) used to surface the most relevant remedies."""
    k = _load()
    if not k:
        return ""
    mech = k.get("mechanics", {}) or {}
    meta = mech.get("meta", {}) or {}
    out: list[str] = [
        f"=== FC 26 COACHING KNOWLEDGE (Title Update {meta.get('current_title_update', '?')}) ===",
        "Ground your coaching in these FACTS. Use the correct FC 26 controls when "
        "prescribing fixes. Do NOT reference removed mechanics (Timed Finishing, "
        "Agile Dribbling). Do not invent controls.",
        "",
        "KEY MECHANICS:",
    ]
    for e in mech.get("entries", []) or []:
        if "removed" in (e.get("tags") or []):
            out.append(f"- {e['title']} — REMOVED in FC 26; never coach it.")
            continue
        ctrl = f" [{e['controls']}]" if e.get("controls") else ""
        out.append(f"- {e['title']}{ctrl}: {' '.join(e.get('detail', '').split())}")

    tac = (k.get("tactics", {}) or {}).get("entries", []) or []
    if tac:
        out.append("")
        out.append("TACTICS (FC IQ):")
        for e in tac:
            out.append(f"- {e['title']}: {' '.join(e.get('detail', '').split())}")

    remedies = (k.get("mistake_remedies", {}) or {}).get("entries", []) or []
    sel = _select_remedies(remedies, hints, max_remedies)
    if sel:
        out.append("")
        out.append("MISTAKE -> FIX + DRILL (use these for weaknesses and 'what to practice'):")
        for e in sel:
            out.append(
                f"- {e['problem']} FIX: {' '.join(e.get('fix', '').split())} "
                f"DRILL: {e.get('drill', '')}"
            )

    patches = (k.get("patch_notes", {}) or {}).get("entries", []) or []
    if patches:
        latest = patches[0]
        out.append("")
        out.append(f"CURRENT META (v{latest.get('version')}): "
                   f"{' '.join(latest.get('summary', '').split())}")

    # Meta principles + formations (teach the game).
    fm = k.get("formations", {}) or {}
    princ = fm.get("principles", []) or []
    if princ:
        out.append("")
        out.append("META PRINCIPLES (how FC 26 is actually won):")
        for e in princ:
            out.append(f"- {e['title']}: {' '.join(str(e.get('detail', '')).split())}")
    forms = fm.get("entries", []) or []
    fsel = _select_remedies(forms, hints, 4) if hints else forms[:4]
    if fsel:
        out.append("")
        out.append("META FORMATIONS:")
        for e in fsel:
            out.append(f"- {e['title']}: {' '.join(str(e.get('detail', '')).split())}")

    # Pro references — surface the most relevant styles (by hints), else the first.
    pros = (k.get("pro_styles", {}) or {}).get("entries", []) or []
    psel = _select_remedies(pros, hints, 2) if hints else pros[:1]
    for pr in psel:
        out.append("")
        out.append(f"PRO REFERENCE — {pr.get('player')}: "
                   f"{' '.join(str(pr.get('summary', '')).split())}")
        for c in (pr.get("coach_with_it") or [])[:3]:
            out.append(f"- {' '.join(str(c).split())}")

    # Auto-researched facts (self-learning). Kept separate + flagged as learned.
    learned = (k.get("learned", {}) or {}).get("entries", []) or []
    lsel = _select_remedies(learned, hints, 5) if hints else learned[:5]
    if lsel:
        out.append("")
        out.append("LEARNED (auto-researched; verify if unsure):")
        for e in lsel:
            out.append(f"- {' '.join(str(e.get('detail', '')).split())}")
    return "\n".join(out)


def issue_tags() -> list[dict]:
    """Controlled vocabulary of weakness tags (the mistake_remedy ids + their
    problem text). The coach tags each match's weaknesses from this list so the
    longitudinal loop can aggregate them across matches consistently."""
    rem = (_load().get("mistake_remedies", {}) or {}).get("entries", []) or []
    return [{"tag": e["id"], "label": e.get("problem", e["id"])} for e in rem if e.get("id")]


def refresh_cache() -> None:
    _load.cache_clear()
