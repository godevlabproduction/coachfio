"""Scoreboard timeline - which reads we trust.

Two real misses are covered here.

  1. A 4-2 match reported as 4-1: the closing goal gets only ONE read because the
     video ends, and the anti-phantom rule dropped it.
  2. A 15-minute 11-3 match reported as 2-5: at one read every 56s a high-scoring
     match changes score between EVERY pair of reads, so no value was ever seen
     twice, every genuine late value was dropped as a singleton, and the true
     full-time score was then rejected as an implausible jump from a stale
     baseline. The error grows with the scoreline.

Both came from testing trust by REPETITION. The property that actually separates a
phantom from a real score is direction: replays and misread crests go BACKWARDS, a
real score never does. So a run is trusted when it does not decrease, the jump is
plausible, and nothing later contradicts it.
"""
from __future__ import annotations

import pytest

from core.pipeline.stages import _trusted_runs


def run(value, start, count):
    return {"v": value, "start": start, "count": count}


def vals(runs):
    return [r["v"] for r in _trusted_runs(runs)]


class TestTrustedRuns:
    def test_late_goal_seen_once_is_admitted(self):
        """4-1 held for a while, then 4-2 in the closing seconds. The video ends,
        so that last plateau can never be seen twice."""
        assert vals([run((4, 1), 0, 5), run((4, 2), 5, 1)]) == [(4, 1), (4, 2)]

    def test_trailing_run_with_no_change_adds_nothing(self):
        assert vals([run((2, 1), 0, 4), run((2, 1), 4, 1)]) == [(2, 1)]

    def test_score_going_backwards_is_rejected(self):
        """Replays show older scores; a real score never decreases."""
        assert vals([run((3, 2), 0, 4), run((1, 0), 4, 1)]) == [(3, 2)]

    @pytest.mark.parametrize("phantom", [(9, 1), (4, 8), (19, 19)])
    def test_implausible_jump_is_rejected(self, phantom):
        """A crest or jersey number misread as a big number must not become goals."""
        assert vals([run((4, 1), 0, 5), run(phantom, 5, 1)]) == [(4, 1)]

    def test_plausible_late_double_is_admitted(self):
        """Two goals inside the final sampling window is unusual but real."""
        assert vals([run((2, 0), 0, 6), run((2, 2), 6, 1)]) == [(2, 0), (2, 2)]

    def test_first_goal_of_the_match_late_on(self):
        """Baseline is 0-0; a leading 0-0 run records nothing and must not crash."""
        assert vals([run((0, 0), 0, 5), run((1, 0), 5, 1)]) == [(1, 0)]

    def test_empty_runs_is_safe(self):
        assert _trusted_runs([]) == []


class TestHighScoringMatch:
    """The 11-3 regression. Reads climb faster than the sampling rate, so almost
    every value is a singleton - but the chain never goes backwards."""

    # Verbatim from the failing match: a 15-minute video sampled every 56s.
    REAL = [run((1, 2), 0, 2), run((2, 2), 2, 1), run((2, 3), 3, 2),
            run((2, 5), 6, 4), run((2, 7), 10, 1), run((3, 8), 12, 1),
            run((3, 10), 13, 1)]

    def test_final_score_is_the_top_of_the_chain_not_the_longest_plateau(self):
        """This returned (2, 5) - the longest plateau - and shipped as the report
        title while the model's own read said 11-3."""
        assert vals(self.REAL)[-1] == (3, 10)

    def test_no_genuine_read_is_discarded(self):
        assert len(vals(self.REAL)) == len(self.REAL)

    def test_goal_count_matches_the_final_score(self):
        h, a = vals(self.REAL)[-1]
        assert h + a == 13

    def test_a_singleton_is_trusted_when_a_later_read_confirms_it(self):
        """(2, 7) is seen once, but (3, 8) follows and is higher on both sides -
        nothing ever contradicted it."""
        assert (2, 7) in vals(self.REAL)

    def test_a_singleton_that_reverts_is_still_rejected(self):
        """The anti-phantom rule must survive the relaxation: a value seen once and
        then contradicted by a LOWER later read is a replay or a misread."""
        runs = [run((2, 5), 0, 3), run((4, 5), 3, 1), run((2, 6), 4, 2)]
        assert vals(runs) == [(2, 5), (2, 6)]


# --- batched scoreboard reads -------------------------------------------------
# The reads were one-image-per-request: ~60 round-trips to read two digits each,
# which was 75% of a match's wall time while costing 15% of its money. They are
# now batched. The risk that buys is misalignment - if a batch's replies are
# short or out of order, every goal after it shifts in time - so each reply
# carries its own index and anything unplaceable is dropped rather than guessed.

def _apply(reads, n_images, start=0):
    """Mirror of the mapping in _read_score_timeline's batch handler."""
    from core.pipeline.stages import _plausible_score

    out = []
    for item in reads:
        if not isinstance(item, dict):
            continue
        i = item.get("i")
        if not isinstance(i, int) or not (0 <= i < n_images):
            continue
        h, a = _plausible_score(item.get("home")), _plausible_score(item.get("away"))
        out.append((start + i, (h, a) if h is not None and a is not None else None))
    return out


def test_batch_reads_map_to_their_own_frame():
    got = _apply([{"i": 0, "home": 1, "away": 0},
                  {"i": 1, "home": 1, "away": 1},
                  {"i": 2, "home": 2, "away": 1}], 3, start=6)
    assert got == [(6, (1, 0)), (7, (1, 1)), (8, (2, 1))]


def test_out_of_order_replies_still_land_correctly():
    """The index is authoritative, not the position in the array - a reordered
    reply must not rewrite the match's goal times."""
    got = dict(_apply([{"i": 2, "home": 2, "away": 1},
                       {"i": 0, "home": 1, "away": 0},
                       {"i": 1, "home": 1, "away": 1}], 3))
    assert got == {0: (1, 0), 1: (1, 1), 2: (2, 1)}


def test_a_short_reply_leaves_gaps_rather_than_shifting():
    """Two entries for three images used to mean the reads slid up by one. Now
    the third frame is simply unknown, which the plateau logic already tolerates."""
    got = _apply([{"i": 0, "home": 1, "away": 0}, {"i": 2, "home": 2, "away": 1}], 3)
    assert got == [(0, (1, 0)), (2, (2, 1))]


def test_unplaceable_or_junk_entries_are_dropped():
    got = _apply([{"i": None, "home": 1, "away": 0},   # no index
                  {"i": 9, "home": 3, "away": 0},      # outside the batch
                  "nonsense",                          # not an object
                  {"i": 1}], 3)                        # scoreboard not readable
    assert got == [(1, None)]


def test_implausible_scores_are_rejected_not_stored():
    """The phantom-digit defence has to survive batching: a crest merging with a
    digit produced reads like 94-8 at full confidence."""
    got = _apply([{"i": 0, "home": 94, "away": 8}, {"i": 1, "home": 2, "away": 1}], 2)
    assert got == [(0, None), (1, (2, 1))]


# --- title vs body agreement --------------------------------------------------
# The match title comes from the deterministic scoreboard timeline; the report's
# MATCH CONTEXT line came from the model's own read while watching. Nothing
# compared them, so a real report shipped titled 5-2 while its first line said
# "11-3 Win". The timeline is the authority.
from core.pipeline.stages import _restate_result


def test_body_result_is_restated_from_the_authoritative_score():
    d = {"match_context": {"result": "11-3 Win", "mode": "Champions"}}
    _restate_result(d, {"score_home": 3, "score_away": 11}, "away")
    assert d["match_context"]["result"] == "11-3 Win"      # already agreed


def test_a_disagreeing_body_result_is_corrected():
    """The exact shipped bug: title 5-2, body 11-3."""
    d = {"match_context": {"result": "11-3 Win"}}
    _restate_result(d, {"score_home": 2, "score_away": 5}, "away")
    assert d["match_context"]["result"] == "5-2 Win"


def test_result_is_written_from_the_players_perspective():
    """Home player losing 2-5 must not be told they won."""
    d = {"match_context": {"result": "whatever"}}
    _restate_result(d, {"score_home": 2, "score_away": 5}, "home")
    assert d["match_context"]["result"] == "2-5 Loss"


def test_draw_is_labelled():
    d = {"match_context": {"result": "x"}}
    _restate_result(d, {"score_home": 3, "score_away": 3}, "away")
    assert d["match_context"]["result"] == "3-3 Draw"


def test_missing_score_leaves_the_body_alone():
    """No authoritative score means nothing to correct with - never blank it."""
    d = {"match_context": {"result": "11-3 Win"}}
    _restate_result(d, {}, "away")
    assert d["match_context"]["result"] == "11-3 Win"


def test_missing_match_context_is_safe():
    d = {}
    _restate_result(d, {"score_home": 1, "score_away": 0}, "home")
    assert d == {}
