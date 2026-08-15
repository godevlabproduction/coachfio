"""Competitive standing -> suggested coaching level.

Division Rivals and FUT Champions are FC concepts, so both the questions and the
mapping live in the adapter - `/core` must never learn what a division is. These
tests pin the rules the player will actually see.
"""
from __future__ import annotations

import pytest

from adapters.ea_fc_26.adapter import EaFc26Adapter

adapter = EaFc26Adapter()


def suggest(division=None, champs=None):
    answers = {}
    if division:
        answers["division"] = division
    if champs:
        answers["champs_wins"] = champs
    return adapter.suggest_skill_level(answers)


class TestSurveyShape:
    def test_offers_division_10_down_to_1_plus_elite(self):
        q = {x["key"]: x for x in adapter.skill_survey()}["division"]
        values = [o["value"] for o in q["options"]]
        assert values[0] == "elite"
        assert [f"div{i}" for i in range(1, 11)] == values[1:]
        assert len(values) == 11

    def test_champs_offers_the_four_win_bands(self):
        q = {x["key"]: x for x in adapter.skill_survey()}["champs_wins"]
        assert [o["value"] for o in q["options"]] == ["1-4", "5-8", "9-12", "13-15"]

    def test_champs_is_locked_for_divisions_7_to_10(self):
        q = {x["key"]: x for x in adapter.skill_survey()}["champs_wins"]
        assert set(q["locked_by"]["values"]) == {"div7", "div8", "div9", "div10"}
        assert q["locked_by"]["key"] == "division"


class TestDivisionOnly:
    @pytest.mark.parametrize("division", ["div10", "div9", "div8", "div7"])
    def test_bottom_divisions_suggest_amateur(self, division):
        assert suggest(division)["level"] == "amateur"

    @pytest.mark.parametrize("division", ["div6", "div5", "div4", "div3"])
    def test_middle_divisions_suggest_intermediate(self, division):
        assert suggest(division)["level"] == "intermediate"

    @pytest.mark.parametrize("division", ["div2", "div1", "elite"])
    def test_top_divisions_suggest_pro(self, division):
        assert suggest(division)["level"] == "pro"

    def test_no_answers_gives_no_suggestion(self):
        assert suggest() is None

    def test_unknown_division_gives_no_suggestion(self):
        assert suggest("div99") is None


class TestChampsRaisesOnly:
    """A bad weekend says little; 13 wins is hard to achieve by accident. So a
    Champs record may only RAISE the suggestion, never lower it."""

    def test_strong_run_lifts_a_mid_division_player_to_pro(self):
        assert suggest("div5", "13-15")["level"] == "pro"

    def test_good_run_lifts_a_higher_division_player_to_pro(self):
        assert suggest("div4", "9-12")["level"] == "pro"

    def test_good_run_does_not_lift_a_lower_division_player_past_intermediate(self):
        assert suggest("div6", "9-12")["level"] == "intermediate"

    def test_weak_run_never_lowers_a_top_division_player(self):
        assert suggest("elite", "1-4")["level"] == "pro"
        assert suggest("div1", "5-8")["level"] == "pro"

    def test_modest_run_still_implies_the_fundamentals(self):
        assert suggest("div6", "5-8")["level"] == "intermediate"


class TestLockedChampsIsIgnored:
    @pytest.mark.parametrize("division", ["div7", "div8", "div9", "div10"])
    def test_champs_cannot_lift_a_locked_division(self, division):
        """The UI disables the question there; the server must not trust a value
        that arrives anyway."""
        assert suggest(division, "13-15")["level"] == "amateur"


class TestSuggestionIsExplained:
    def test_every_suggestion_carries_a_reason(self):
        for d in ["div10", "div5", "elite"]:
            s = suggest(d)
            assert s["reason"] and isinstance(s["reason"], str)

    def test_reason_reflects_champs_when_champs_decided_it(self):
        assert "Champs" in suggest("div5", "13-15")["reason"]
