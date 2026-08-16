"""Which game this hostname serves.

One subdomain per game: fifa.coachfio.com is the FC site, the bare domain is the
chooser. The frontend is one build served on every host, so it asks here at boot
what it is rather than hardcoding a game id.

The mapping lives in settings as a lookup table, never as a branch. `/core` must
not learn a game id (tests/test_core.py fails the build if one appears), and the
same rule is worth keeping here: adding a game should be a config line plus a
plugin, not an edit to this file.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from adapters.base.registry import UnknownGameError, registry
from core.config import get_settings

router = APIRouter(prefix="/api/site", tags=["site"])


# Suffixes that are a whole "domain" on their own, so one label in front of them
# is already a subdomain. Without this, fifa.localhost looks like a bare domain
# (two labels, same shape as coachfio.com) and resolves to no game - which sent
# the chooser on to fifa.fifa.localhost. Browsers resolve *.localhost to 127.0.0.1
# natively, so it is the first thing anyone tries locally.
_DEV_SUFFIXES = {"localhost", "local", "test", "internal", "localdomain"}


def _split_host(host: str) -> list[str]:
    h = (host or "").split(",")[0].strip().lower()
    h = h.rsplit(":", 1)[0] if h.count(":") == 1 else h   # strip port, keep IPv6
    h = h.strip("[]").rstrip(".")
    return h.split(".") if h else []


def host_label(host: str, root_labels: set[str]) -> str | None:
    """First DNS label of a Host header, or None when it addresses the root site.

    'fifa.coachfio.com' and 'fifa.localhost' both mean the fifa site.
    'coachfio.com', 'localhost', '127.0.0.1' and a missing header all mean the
    root, so the caller shows the chooser rather than guessing a game.
    """
    parts = _split_host(host)
    if not parts or all(p.isdigit() for p in parts):
        return None                      # bare IPv4 has no meaningful label
    has_sub = len(parts) >= 3 or (len(parts) == 2 and parts[-1] in _DEV_SUFFIXES)
    if not has_sub:
        return None
    label = parts[0]
    return None if label in root_labels else label


def host_root(host: str, root_labels: set[str]) -> str:
    """The host WITHOUT its game label, e.g. fifa.localhost -> localhost.

    Computed here rather than in the browser. The client used to derive it by
    counting dots, which is the same assumption that broke on fifa.localhost, and
    duplicating the rule guarantees the two drift.
    """
    parts = _split_host(host)
    if not parts:
        return ""
    if host_label(host, root_labels) is None:
        return ".".join(parts)
    return ".".join(parts[1:])


def resolve_game(request: Request):
    """(game_id, edition) for this host, or None for the root site."""
    s = get_settings()
    label = host_label(request.headers.get("host", ""), s.root_label_set)
    if not label:
        return None
    return s.site_host_map.get(label)


def _game_info(pair) -> dict | None:
    if not pair:
        return None
    gid, edition = pair
    try:
        ident = registry.get(gid, edition).identity()
    except UnknownGameError:
        # Configured for a game that is not installed. Report it as unresolved
        # rather than inventing a name; the chooser is a safe fallback.
        return None
    return {"game_id": ident.game_id, "edition": ident.edition,
            "display_name": ident.display_name, "franchise": ident.franchise}


@router.get("")
def site(request: Request) -> dict:
    """What this hostname serves.

    `game` is null on the root domain, which is the signal for the frontend to
    show the chooser instead of a game-specific home page.
    """
    s = get_settings()
    host = (request.headers.get("host") or "").split(":")[0]
    sites = []
    # Declared order, not alphabetical. SITE_HOSTS lists the games in the order
    # they should be shown, which is how the chooser is kept in step with the rest
    # of the page: sorting here put "cs" before "fifa" and silently disagreed with
    # the screenshots above it. Reordering the config now reorders the chooser.
    for label, pair in s.site_host_map.items():
        info = _game_info(pair)
        if info:
            sites.append({"label": label, **info})
    raw = request.headers.get("host", "")
    return {
        "host": host,
        "label": host_label(raw, s.root_label_set),
        # The base every sibling site hangs off. Served so the client never has to
        # work it out from the URL.
        "root": host_root(raw, s.root_label_set),
        "game": _game_info(resolve_game(request)),
        "sites": sites,
    }
