"""Subdomain -> game resolution.

One subdomain per game (fifa.coachfio.com). The mapping is settings data rather
than a branch, for the same reason `/core` may not name a game: adding a title
should be a config line plus a plugin, never an edit to routing code.

The failure that matters here is a host resolving to the WRONG game. Every match,
report and metric would be filed against another title's adapter, and nothing
would look broken until someone read the output.
"""
from __future__ import annotations

import pytest

from api.routes.site import host_label

ROOT = {"www", "app", "coachfio", "localhost", "127"}


@pytest.mark.parametrize("host,expected", [
    ("fifa.coachfio.com", "fifa"),
    ("FIFA.CoachFio.com", "fifa"),          # Host headers are not case-normalised
    ("fifa.coachfio.com:8000", "fifa"),     # port must not become part of the label
    ("fifa.coachfio.com.", "fifa"),         # fully-qualified trailing dot
    ("cs.coachfio.com", "cs"),
])
def test_subdomain_selects_its_site(host, expected):
    assert host_label(host, ROOT) == expected


@pytest.mark.parametrize("host", [
    "coachfio.com",          # the root site: chooser, not a game
    "www.coachfio.com",
    "app.coachfio.com",
    "localhost:8000",        # single label
    "127.0.0.1:8000",        # bare IPv4 has no meaningful label
    "",                      # missing Host header
])
def test_root_and_bare_hosts_have_no_game(host):
    assert host_label(host, ROOT) is None


def test_unmapped_subdomain_does_not_borrow_another_game():
    """An unknown label must fall through to the chooser. Silently serving the
    only installed game would file a CS2 upload against the football adapter."""
    from core.config import Settings
    s = Settings(site_hosts="fifa=ea-fc@26")
    assert host_label("valorant.coachfio.com", ROOT) == "valorant"
    assert s.site_host_map.get("valorant") is None


def test_mapping_is_data_not_code():
    from core.config import Settings
    s = Settings(site_hosts="fifa=ea-fc@26,cs=cs2@2")
    assert s.site_host_map == {"fifa": ("ea-fc", "26"), "cs": ("cs2", "2")}


def test_a_typo_in_the_env_var_does_not_take_the_api_down():
    """Malformed entries are skipped, not raised: a bad env var should degrade to
    the chooser rather than stop the service booting."""
    from core.config import Settings
    s = Settings(site_hosts="fifa=ea-fc@26,broken,alsobroken=,=nope,cs=cs2@2")
    assert s.site_host_map == {"fifa": ("ea-fc", "26"), "cs": ("cs2", "2")}


# --- *.localhost, and why the root must come from the server ------------------
# Reported live: visiting fifa.localhost:8000 produced links to
# fifa.fifa.localhost:8000. One assumption, wrong in two places - that a host
# needs three labels to have a subdomain. fifa.localhost has two, so it resolved
# to NO game, which dropped the page into the chooser, which then appended "fifa."
# to the whole host. Browsers resolve *.localhost to 127.0.0.1 with no setup, so
# it is the first host anyone tries.

from api.routes.site import host_root


@pytest.mark.parametrize("host,expected", [
    ("fifa.localhost", "fifa"),
    ("fifa.localhost:8000", "fifa"),
    ("cs.localhost", "cs"),
    ("fifa.local", "fifa"),
    ("fifa.test", "fifa"),
])
def test_dev_subdomains_resolve_to_their_game(host, expected):
    assert host_label(host, ROOT) == expected


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "coachfio.com"])
def test_bare_dev_and_apex_hosts_stay_on_the_chooser(host):
    assert host_label(host, ROOT) is None


@pytest.mark.parametrize("host,root", [
    ("fifa.localhost:8000", "localhost"),
    ("fifa.coachfio.com", "coachfio.com"),
    ("coachfio.com", "coachfio.com"),      # already the root
    ("localhost:8000", "localhost"),
    ("fifa.staging.coachfio.com", "staging.coachfio.com"),
])
def test_root_strips_only_the_game_label(host, root):
    assert host_root(host, ROOT) == root


def test_sibling_links_never_double_the_label():
    """The actual reported bug: label + root must not reproduce the label twice."""
    for host in ("fifa.localhost:8000", "fifa.coachfio.com", "coachfio.com"):
        link = "fifa." + host_root(host, ROOT)
        assert not link.startswith("fifa.fifa."), link
