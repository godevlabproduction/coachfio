"""FC 26 knowledge base (Layer 1 'brain'): loads the distilled YAML knowledge and
builds a compact, RELEVANT playbook string to ground the coaching model on real,
current FC 26 mechanics/tactics/remedies - instead of generic football advice.

Game-specific by design: this lives in the adapter, never in core.
"""
from __future__ import annotations

import functools
import math
import re
from collections import Counter
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
    """Add (or replace) a distilled fact in learned.yaml - the growth file that
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


# Generic English function words. Deliberately does NOT include football-loaded
# positional words (back, through, over, near, wide, side...) - "tracking BACK"
# and "played THROUGH" are exactly the signal we're trying to catch.
_STOP = frozenset("""
about after again against all also and any are because been before being both but
came can could did does doing each even every few for from get gets getting got
had has have his her him how its just like made make makes many more most much
must need needs not now once one only onto other our own play played player
players plays put ran same she should since some still such take takes than that
the their them then there these they this those time times too took two until
upon use used uses very was way well went were what when where which while who
whom why will with within without would you your
""".split())

# The knowledge files were written with US spelling ("center back", "defense")
# while a coaching observation log often comes back in UK spelling. Without this
# the two never overlap and the most relevant facts score zero.
# UK/US spelling ONLY. Do not add morphological collapses here (defender ->
# defense and friends): folding a whole word family onto one token makes it
# appear in nearly every entry, IDF correctly drops its weight to ~0, and the
# scorer loses exactly the signal it needed to rank defending facts.
_VARIANTS = {
    "centre": "center", "defence": "defense", "offence": "offense",
    "metre": "meter", "behaviour": "behavior", "favour": "favor",
}


def _stem(w: str) -> str:
    """Crude suffix strip so jockeying/jockey and presses/press collide.

    Deliberately does NOT strip -er/-ers: in English that mangles far too many
    football words (center->cent, shoulder->should, counter->count,
    corner->corn). Plural/participle suffixes are safe enough; -er is not.
    """
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _norm(w: str) -> str:
    """Spelling variant -> stem -> variant again, so both `defending` and
    `defenders` land on the same token as `defense`."""
    w = _VARIANTS.get(w, w)
    w = _stem(w)
    return _VARIANTS.get(w, w)


# Position abbreviations are the densest signal in an observation log ("the CB
# stepped up", "your CDM drifts forward") and every one of them is shorter than
# the length filter, so they must be handled explicitly. Expanding them to the
# words the knowledge files actually use is what lets a log about "CB" match a
# fact about "center backs".
_ABBREV = {
    "gk": "goalkeeper keeper",
    "cb": "center back defense",
    "lb": "fullback defense", "rb": "fullback defense",
    "lwb": "wingback", "rwb": "wingback", "wb": "wingback",
    "cdm": "midfield defense holding", "cm": "midfield", "cam": "midfield attack",
    "lm": "midfield wide", "rm": "midfield wide",
    "lw": "winger wide attack", "rw": "winger wide attack",
    "st": "striker forward attack", "cf": "striker forward attack",
}


def _keywords(text: str) -> set[str]:
    """Normalised content words from a blob, for overlap scoring."""
    out: set[str] = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        exp = _ABBREV.get(w)
        if exp:
            out.update(_norm(x) for x in exp.split())
            continue
        if len(w) < 4 or w in _STOP:
            continue
        out.add(_norm(w))
    return out


def _entry_words(e: dict) -> set[str]:
    """All searchable text on an entry. The id is a slug of the original title,
    so it carries real topic words even when there is no `title` field."""
    return _keywords(
        str(e.get("id", "")).replace("-", " ") + " "
        + str(e.get("title", "")) + " "
        + str(e.get("problem", "")) + " "
        + str(e.get("detail", ""))
    )


def _select_remedies(entries: list[dict], hints: str, n: int) -> list[dict]:
    """Pick the entries most relevant to the detected observations.

    Scoring is layered because the knowledge files are not uniform:
      - `match` phrases (curated prose fragments) are the strongest signal, but
        only `mistake_remedies` and `formations` have them;
      - tags help, but only when the log happens to contain the literal tag word;
      - CONTENT OVERLAP on the entry's own text is the fallback that makes
        `learned.yaml` reachable at all.

    That last layer matters: `add_learned()` writes no `match` field and an
    observation log is natural prose ("CB stepped up and got played through"),
    which almost never contains a bare tag token like "defending". Without it
    every learned fact scored 0, selection fell through to `entries[:n]`, and the
    coach got the same first few entries on every single match - see
    tests/test_knowledge.py.
    """
    if not hints:
        return entries[:n]
    h = hints.lower()
    hw = _keywords(hints)

    docs = [_entry_words(e) for e in entries]
    total = len(docs) or 1
    df = Counter(w for d in docs for w in d)
    # Rare-in-corpus words carry the meaning. Without this, long entries win every
    # query just by overlapping more generic words ("player", "position", "role").
    idf = {w: math.log(1 + total / (1 + c)) for w, c in df.items()}

    scored = []
    for e, own in zip(entries, docs, strict=True):
        score = 4.0 * sum(1 for m in e.get("match", []) if str(m).lower() in h)
        score += 2.0 * sum(1 for t in e.get("tags", []) if str(t).lower() in h)
        # Normalise by entry length, or a long rambling entry outranks a precise
        # one simply by overlapping more words.
        overlap = sum(idf.get(w, 0.0) for w in (own & hw))
        score += overlap / math.sqrt(len(own) or 1)
        scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    picked = [e for s, e in scored if s > 0][:n]
    return picked or entries[:n]


def build_playbook(hints: str = "", max_remedies: int = 6, max_learned: int = 12) -> str:
    """A compact FC-26 grounding block for the coaching prompt. `hints` is any text
    (e.g. the observation log) used to surface the most relevant remedies."""
    k = _load()
    if not k:
        return ""
    mech = k.get("mechanics", {}) or {}
    meta = mech.get("meta", {}) or {}
    out: list[str] = [
        f"=== FC 26 COACHING KNOWLEDGE (Title Update {meta.get('current_title_update', '?')}) ===",
        # Parenthesised so the implicit concatenation is unmistakably deliberate -
        # inside a list literal, a dropped comma looks exactly the same.
        ("Ground your coaching in these FACTS. Use the correct FC 26 controls when "
         "prescribing fixes. Do NOT reference removed mechanics (Timed Finishing, "
         "Agile Dribbling). Do not invent controls."),
        "",
        "KEY MECHANICS:",
    ]
    for e in mech.get("entries", []) or []:
        if "removed" in (e.get("tags") or []):
            out.append(f"- {e['title']} - REMOVED in FC 26; never coach it.")
            continue
        ctrl = f" [{e['controls']}]" if e.get("controls") else ""
        out.append(f"- {e['title']}{ctrl}: {' '.join(e.get('detail', '').split())}")

    # HOW to think, not WHAT to cite. Always included in full: it changes the
    # shape of every point, so hint-selecting it would silently drop the part
    # that stops the coach prescribing a formation change for a late switch.
    fw = k.get("analysis_framework", {}) or {}
    errs = fw.get("error_types", []) or []
    if errs:
        out.append("")
        out.append(
            "CLASSIFY EVERY MISTAKE - say which TYPE it is before giving the fix, "
            "because the fix differs completely:"
        )
        for e in errs:
            out.append(f"- {e.get('title')}: {' '.join(str(e.get('detail', '')).split())}")
    kinds = fw.get("defensive_error_kinds", []) or []
    if kinds:
        out.append("Name the exact defensive error, never just 'bad defending': "
                   + "; ".join(str(x) for x in kinds) + ".")
    outcomes = fw.get("possession_outcomes", []) or []
    if outcomes:
        out.append("Describe a possession as one of: "
                   + "; ".join(str(x) for x in outcomes) + ".")
    traits = fw.get("elite_traits", []) or []
    if traits:
        out.append("Praise precisely, using the evidence for the trait: "
                   + "; ".join(f"{t.get('trait')} ({t.get('look_for')})" for t in traits))
    example = str(fw.get("worked_example") or "").strip()
    if example:
        out.append("A defensive note should read like this (shape only - never "
                   "reuse these invented specifics): " + " ".join(example.split()))

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
        # Framing matters enormously here. Presented as "use these for weaknesses"
        # this list became a MENU: the model picked plausible-sounding problems
        # off it and attributed them to the match, producing confident notes about
        # things that never happened. It is a phrasing reference for a mistake you
        # actually saw - never a source of mistakes.
        out.append(
            "REFERENCE - common mistakes and their fixes. Do NOT pick a weakness from "
            "this list. Only report what you actually SAW in the video; if you saw it, "
            "use the matching FIX/DRILL below to phrase the remedy:"
        )
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

    # Pro references - surface the most relevant styles (by hints), else the first.
    pros = (k.get("pro_styles", {}) or {}).get("entries", []) or []
    psel = _select_remedies(pros, hints, 2) if hints else pros[:1]
    for pr in psel:
        out.append("")
        out.append(f"PRO REFERENCE - {pr.get('player')}: "
                   f"{' '.join(str(pr.get('summary', '')).split())}")
        for c in (pr.get("coach_with_it") or [])[:3]:
            out.append(f"- {' '.join(str(c).split())}")

    # Player toolkits for the positions this match actually involved. Hint-gated:
    # six full profiles on every report would crowd out the observations, and a
    # squad-fit point is only ever worth making when the footage shows the
    # mismatch.
    profiles = (k.get("player_profiles", {}) or {}).get("entries", []) or []
    psel2 = _select_remedies(profiles, hints, 2) if hints else []
    if psel2:
        out.append("")
        out.append(
            "PLAYER TOOLKITS (the best player is not the highest overall). Only "
            "raise player fit if the video actually shows the mismatch:"
        )
        for e in psel2:
            out.append(f"- {e['title']}: {' '.join(str(e.get('detail', '')).split())}")

    # Ingested facts: tutorial videos (tools/learn_from_video.py), distilled notes
    # and auto-research. This is the biggest and fastest-growing file, so it gets
    # the widest slice - a few hundred extra text tokens is nothing next to the
    # video itself, and these are the most specific facts the coach has.
    learned = (k.get("learned", {}) or {}).get("entries", []) or []
    lsel = _select_remedies(learned, hints, max_learned) if hints else learned[:max_learned]
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
