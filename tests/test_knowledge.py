"""FC 26 knowledge brain - retrieval regression tests.

These exist because of a silent failure that made every coaching report worse:
`add_learned()` writes no `match` field, and the old scorer only matched literal
`match`/tag tokens against the observation log. An observation log is natural
prose ("the CB stepped up and got played through"), so every learned fact scored
0, selection fell through to `entries[:n]`, and the coach received the SAME first
few entries on every match - none of them chosen for relevance.

Nothing raised an error; the reports were just quietly generic. Hence these.
"""
from __future__ import annotations

from adapters.ea_fc_26 import knowledge_base as kb

DEFENDING_LOG = (
    "08:22 the CB stepped up to press and the striker ran the channel behind him. "
    "22:30 your CDM drifts too far forward, nobody screening the back four. "
    "67:15 pressed with the near centre-back again and got played through. "
    "Conceded twice from balls in behind."
)
ATTACKING_LOG = (
    "The opponent sat deep in a compact low block all match. Your attackers stood "
    "still with no runs in behind; you passed sideways across the edge of the box "
    "and never got a shot away."
)


def _entries(name: str) -> list[dict]:
    return (kb._load().get(name, {}) or {}).get("entries", []) or []


def test_position_abbreviations_expand_to_knowledge_vocabulary():
    """Position abbreviations are shorter than the length filter, so without
    explicit handling every one of them is dropped - losing the densest signal in
    a match log. They must also expand to the words the YAML files actually use."""
    words = kb._keywords("the CB was beaten and the CDM was too high")
    assert "center" in words and "back" in words, "CB must reach 'center back' facts"
    assert "midfield" in words, "CDM must reach midfield facts"

    # Short tokens that are NOT abbreviations are still filtered out.
    assert "was" not in words


def test_uk_us_spelling_and_stemming_collide():
    """The knowledge files use US spelling; logs often come back in UK spelling."""
    assert kb._keywords("centre") == kb._keywords("center")
    # Crude stemming so jockeying/jockey and presses/press match.
    assert kb._keywords("jockeying") == kb._keywords("jockey")


def test_selection_is_driven_by_hints_not_file_order():
    """The original bug: everything scored 0, so selection silently returned the
    first N entries for every possible match."""
    learned = _entries("learned")
    assert len(learned) >= 10, "expected a populated knowledge brain"

    picked = [e["id"] for e in kb._select_remedies(learned, DEFENDING_LOG, 12)]
    file_order = [e["id"] for e in learned[:12]]
    assert picked != file_order, "selection fell back to file order - retrieval is dead"


def test_different_observations_select_different_knowledge():
    """A defending match and an attacking match must not get the same facts."""
    learned = _entries("learned")
    d = [e["id"] for e in kb._select_remedies(learned, DEFENDING_LOG, 8)]
    a = [e["id"] for e in kb._select_remedies(learned, ATTACKING_LOG, 8)]
    assert d != a, "retrieval does not discriminate between match types"


def test_curated_match_phrases_still_win():
    """`mistake_remedies` carry hand-written `match` phrases; those are the
    strongest signal and must outrank fuzzy content overlap."""
    remedies = _entries("mistake_remedies")
    top = kb._select_remedies(remedies, "the defender kept diving in and over-committing", 1)
    assert top[0]["id"] == "overcommit_cb"


def test_playbook_carries_a_useful_slice_of_the_brain():
    """The playbook previously exposed only 5 of 40 learned facts. Widen it, but
    keep it bounded so the prompt cannot grow without limit."""
    learned = _entries("learned")
    pb = kb.build_playbook(DEFENDING_LOG)
    assert "FC 26 COACHING KNOWLEDGE" in pb
    assert "LEARNED" in pb

    included = sum(1 for e in learned if str(e.get("detail", ""))[:60] in pb)
    assert included > 5, f"only {included} learned facts reached the prompt"
    assert len(pb) < 40_000, "playbook is unbounded"


def test_removed_mechanics_are_always_flagged():
    """FC 26 removed Timed Finishing and Agile Dribbling. The coach must never
    prescribe them, so the playbook has to keep carrying the explicit warning."""
    pb = kb.build_playbook(DEFENDING_LOG)
    assert "Timed Finishing" in pb and "Agile Dribbling" in pb
    assert "REMOVED" in pb


# --- analysis framework + player profiles ------------------------------------
# Added with the competitive-meta ingest. The framework is the one knowledge file
# that must appear UNCONDITIONALLY: it changes how every point is phrased rather
# than supplying a fact to cite, so hint-gating it would silently drop it on
# exactly the matches whose logs happen not to overlap its wording.

def test_error_taxonomy_is_always_in_the_prompt():
    """Without this the coach prescribes a formation change for what was really a
    late player switch - the failure the taxonomy exists to prevent."""
    for hints in ["", DEFENDING_LOG, ATTACKING_LOG]:
        pb = kb.build_playbook(hints=hints)
        assert "CLASSIFY EVERY MISTAKE" in pb
        for kind in ("Decision error", "Execution error", "Tactical error",
                     "Mechanical error", "External factor"):
            assert kind in pb, f"{kind} missing for hints={hints[:20]!r}"


def test_defensive_errors_must_be_named_specifically():
    pb = kb.build_playbook(hints=DEFENDING_LOG)
    assert "never just 'bad defending'" in pb
    assert "Bad player switching" in pb and "Allowing an easy cutback" in pb


def test_player_profiles_are_hint_gated():
    """Six full toolkits on every report would crowd out the observations, and a
    squad-fit point is only worth making when the footage shows the mismatch."""
    assert "PLAYER TOOLKITS" not in kb.build_playbook(hints="")
    assert "PLAYER TOOLKITS" in kb.build_playbook(hints=DEFENDING_LOG)


def test_player_profiles_select_the_position_actually_seen():
    """The toolkit that surfaces should be the position the log is about.

    Note what is deliberately NOT asserted: that an irrelevant profile is absent.
    A defending log routinely names the OPPONENT's striker ("the striker ran the
    channel"), and a lexical scorer cannot tell whose striker it was. Selecting it
    is correct given the text; the block is capped and prefixed with "only raise
    player fit if the video shows the mismatch" precisely because of that.
    """
    profiles = _entries("player_profiles")
    cases = [
        ("Your fullback got dragged inside and they cut the ball back across the "
         "six-yard box.", "fullback_profile"),
        ("Your midfield never screened the pass and the CAM had time between the "
         "lines.", "midfield_profile"),
    ]
    for log, expected in cases:
        assert kb._select_remedies(profiles, log, 2)[0]["id"] == expected, log


def test_player_profile_block_stays_capped():
    """Prompt budget: the block is a hint, not the whole squad-building guide."""
    pb = kb.build_playbook(hints=DEFENDING_LOG)
    assert pb.count(" toolkit: ") <= 2


def test_new_remedies_are_reachable_from_natural_prose():
    """Each new remedy is also a weakness TAG, so one that retrieval can never
    surface is a tag the longitudinal loop will never see used."""
    cases = [
        ("You reached the box then rushed the final pass every time.", "rushed_final_pass"),
        ("Held sprint constantly and the touches ran away from you.", "sprint_overuse"),
        ("You selected the wrong defender and switched far too late.", "poor_player_switching"),
        ("Both fullbacks were caught forward and they countered you.", "no_rest_defence"),
    ]
    remedies = _entries("mistake_remedies")
    for log, expected in cases:
        picked = [e["id"] for e in kb._select_remedies(remedies, log, 6)]
        assert expected in picked, f"{expected!r} unreachable from {log!r} (got {picked})"


def test_weakness_tag_ids_are_unique():
    """issue_tags() is keyed on the remedy id, and the cross-match 'what keeps
    costing you' aggregation counts by that key - a duplicate would split one
    habit across two rows, or silently shadow the other entry's label."""
    ids = [e["id"] for e in _entries("mistake_remedies")]
    assert len(ids) == len(set(ids))
    tags = {t["tag"] for t in kb.issue_tags()}
    assert tags == set(ids)


def test_lengthy_thresholds_are_available_to_the_coach():
    pb = kb.build_playbook(hints="my striker never got there, he looked slow off the mark")
    assert "Lengthy" in pb
    assert "does not make them faster everywhere" in pb
