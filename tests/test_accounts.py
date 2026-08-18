"""Accounts: skill-level parsing and the report calibration it drives.

Pure-logic only - no DB, matching the rest of the suite. Cross-account isolation
is enforced in `api/routes/matches.py::_owned` and `MatchRepository.list(identity=)`
and is exercised against a live stack.
"""
from __future__ import annotations

import pytest

from core.models.enums import SkillLevel
from core.pipeline.stages import _player_block
from core.storage.users import normalise_email, valid_email


class TestEmailIdentity:
    """Email is how a person finds their account again, so it has to match
    regardless of how they typed it."""

    @pytest.mark.parametrize("raw", ["Ilija@Example.com", "  ilija@example.com  ", "ILIJA@EXAMPLE.COM"])
    def test_normalisation_collapses_case_and_space(self, raw):
        assert normalise_email(raw) == "ilija@example.com"

    def test_normalising_nothing_is_empty_not_none(self):
        assert normalise_email(None) == ""

    @pytest.mark.parametrize("email", ["a@b.co", "first.last+tag@sub.example.com"])
    def test_accepts_real_addresses(self, email):
        assert valid_email(email)

    @pytest.mark.parametrize("email", ["", "nope", "a@b", "a@@b.co", "a b@c.co", "@b.co", "a@.co"])
    def test_rejects_junk(self, email):
        assert not valid_email(email)


class TestSkillLevelParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("amateur", SkillLevel.AMATEUR),
        ("Casual", SkillLevel.AMATEUR),          # the label the old upload form sent
        ("beginner", SkillLevel.AMATEUR),
        ("intermediate", SkillLevel.INTERMEDIATE),
        ("Competitive", SkillLevel.INTERMEDIATE),
        ("pro", SkillLevel.PRO),
        ("Elite", SkillLevel.PRO),
        ("ADVANCED", SkillLevel.PRO),
    ])
    def test_known_and_legacy_labels(self, raw, expected):
        assert SkillLevel.parse(raw) is expected

    @pytest.mark.parametrize("raw", ["", None, "   ", "banana", 7])
    def test_unknown_falls_back_to_intermediate(self, raw):
        """A junk or missing value must still produce a usable report, not crash."""
        assert SkillLevel.parse(raw) is SkillLevel.INTERMEDIATE

    def test_explicit_default_is_respected(self):
        assert SkillLevel.parse(None, default=SkillLevel.AMATEUR) is SkillLevel.AMATEUR


class TestReportCalibration:
    """The point of the feature: the same footage must produce a differently
    pitched report depending on who is reading it."""

    def test_each_level_produces_a_different_brief(self):
        briefs = {lvl: _player_block({"skill_level": lvl})
                  for lvl in ("amateur", "intermediate", "pro")}
        assert len(set(briefs.values())) == 3

    def test_amateur_forbids_jargon_and_caps_the_list(self):
        b = _player_block({"skill_level": "amateur"})
        assert "BEGINNER" in b
        assert "No jargon" in b
        assert "3-4 points" in b

    def test_pro_skips_fundamentals(self):
        b = _player_block({"skill_level": "pro"})
        assert "Skip all fundamentals" in b
        assert "5-7 points" in b

    def test_intermediate_does_not_teach_basics(self):
        b = _player_block({"skill_level": "intermediate"})
        assert "do not teach the basics" in b

    def test_missing_skill_still_yields_a_brief(self):
        """capture{} happens for matches created before accounts existed."""
        b = _player_block({})
        assert "HOW TO PITCH THIS REPORT" in b
        assert "INTERMEDIATE" in b

    def test_none_capture_is_safe(self):
        assert "HOW TO PITCH THIS REPORT" in _player_block(None)

    def test_control_scheme_is_named_when_known(self):
        b = _player_block({"skill_level": "pro", "control_scheme": "Alternate"})
        assert "Alternate controls" in b

    def test_control_scheme_omitted_when_unknown(self):
        assert "controls" not in _player_block({"skill_level": "pro"}).split("\n")[-2]


class TestCoachAthleteContext:
    """Coach-uploaded footage carries `capture.athlete`. The report must then be
    written ABOUT that player in the third person - 'you dived in' addresses the
    wrong human when a coach is reading. DB-backed link/chat behaviour is
    exercised against the live stack, matching this suite's no-DB convention."""

    def test_athlete_switches_the_report_to_third_person(self):
        block = _player_block({"skill_level": "pro", "athlete": "Marko"})
        assert "THIRD person" in block
        assert "Marko" in block
        assert "READER IS A COACH" in block

    def test_no_athlete_stays_second_person(self):
        assert "THIRD person" not in _player_block({"skill_level": "pro"})

    def test_athlete_is_orthogonal_to_skill_level(self):
        """Role must not hijack the skill axis: an amateur coach still gets the
        amateur pitch, just in the third person."""
        block = _player_block({"skill_level": "amateur", "athlete": "Marko"})
        assert "BEGINNER" in block          # the amateur brief survives
        assert "THIRD person" in block

    def test_blank_athlete_is_ignored(self):
        assert "THIRD person" not in _player_block({"athlete": "   "})


# --- provider linking ---------------------------------------------------------
# The rule that decides whether an account created BEFORE the auth provider
# existed keeps its matches, or is stranded while a duplicate takes its place.
# No DB, matching the rest of this file: the lookups are stubbed and what is
# under test is which branch is taken.

class _Row:
    def __init__(self, user_id, email="", subject=None):
        self.user_id = user_id
        self.email = email
        self.auth_subject = subject


@pytest.fixture()
def linking(monkeypatch):
    """Stub the four lookups `link_or_create_from_provider` builds on, and record
    what it decided to do."""
    from core.storage import users as u

    state = {"by_subject": {}, "by_email": {}, "created": [], "linked": []}

    def find_by_auth_subject(_s, subject):
        return state["by_subject"].get(subject)

    def find_by_email(_s, email):
        return state["by_email"].get(email)

    def create_user(_s, email, display_name=None, skill_level=None):
        row = _Row(f"new-{len(state['created'])}", email)
        state["created"].append(row)
        state["by_email"][email] = row
        return row

    def link_auth_subject(_s, user_id, subject):
        owner = state["by_subject"].get(subject)
        if owner is not None and owner.user_id != user_id:
            raise ValueError("that login is already linked to another account")
        row = next((r for r in list(state["by_email"].values()) + state["created"]
                    if r.user_id == user_id), _Row(user_id))
        row.auth_subject = subject
        state["by_subject"][subject] = row
        state["linked"].append((user_id, subject))
        return row

    monkeypatch.setattr(u, "find_by_auth_subject", find_by_auth_subject)
    monkeypatch.setattr(u, "find_by_email", find_by_email)
    monkeypatch.setattr(u, "create_user", create_user)
    monkeypatch.setattr(u, "link_auth_subject", link_auth_subject)
    return state


def test_a_verified_email_adopts_the_existing_account(linking):
    """The founding account owns 15 matches keyed on its user_id. Signing in with
    Google on the same address must land on THAT account, not a new one."""
    from core.storage.users import link_or_create_from_provider

    existing = _Row("founder", "player@example.com")
    linking["by_email"]["player@example.com"] = existing

    got = link_or_create_from_provider(None, subject="sub-1",
                                       email="player@example.com",
                                       email_verified=True)

    assert got.user_id == "founder", "the existing account (and its matches) was stranded"
    assert not linking["created"], "a duplicate account was made instead of adopting"


def test_an_unverified_email_never_adopts_an_account(linking):
    """Otherwise anyone could take over an account by signing up with its address
    at a provider that does not confirm ownership. A duplicate is recoverable; a
    stolen account is not."""
    from core.storage.users import link_or_create_from_provider

    victim = _Row("victim", "victim@example.com")
    linking["by_email"]["victim@example.com"] = victim

    got = link_or_create_from_provider(None, subject="sub-attacker",
                                       email="victim@example.com",
                                       email_verified=False)

    assert got.user_id != "victim", "an unverified email took over an account"
    assert linking["created"], "expected a fresh account instead"


def test_the_same_subject_always_returns_the_same_account(linking):
    """Every sign-in after the first. If this created a second account, a user
    would silently lose their history on their second visit."""
    from core.storage.users import link_or_create_from_provider

    first = link_or_create_from_provider(None, subject="sub-stable",
                                         email="a@example.com", email_verified=True)
    second = link_or_create_from_provider(None, subject="sub-stable",
                                          email="changed@example.com",
                                          email_verified=True)

    assert first.user_id == second.user_id
    assert len(linking["created"]) == 1


def test_a_subject_is_required(linking):
    """A provider that returns no subject must fail loudly, not create an
    unreachable account."""
    from core.storage.users import link_or_create_from_provider

    with pytest.raises(ValueError):
        link_or_create_from_provider(None, subject="", email="x@example.com",
                                     email_verified=True)
