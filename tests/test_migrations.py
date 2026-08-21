"""Schema-history guards. Pure filesystem - no database, like the rest of the
suite. The migrations themselves run against Postgres via init_db() on boot;
what CAN go wrong at build time is the history itself: two heads after a bad
merge, a revision that fell out of the chain, or the baseline drifting away
from the id init_db() stamps pre-Alembic databases with."""
from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parent.parent


def _script_dir() -> ScriptDirectory:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return ScriptDirectory.from_config(cfg)


def test_history_is_a_single_chain():
    """Exactly one head. Two heads happen when two branches each add a migration
    and nobody merges them - upgrade then fails at boot for everyone."""
    heads = _script_dir().get_heads()
    assert len(heads) == 1, f"multiple migration heads: {heads}"


def test_baseline_is_the_root_and_matches_the_stamp():
    """init_db() stamps pre-Alembic databases with core.storage.db._BASELINE_REV;
    if the root revision and that constant drift apart, every existing dev DB
    would replay DDL it already has and fail on the first CREATE TABLE."""
    from core.storage.db import _BASELINE_REV

    script = _script_dir()
    root = [r for r in script.walk_revisions() if r.down_revision is None]
    assert len(root) == 1
    assert root[0].revision == _BASELINE_REV


def test_survey_nesting_migration_is_in_history():
    revisions = {r.revision for r in _script_dir().walk_revisions()}
    assert "0002" in revisions


class TestSurveyMerge:
    """The write path behind the 0002 shape: answers merge under an opaque
    "<game_id>@<edition>" namespace, so two games can never collide."""

    def test_two_games_do_not_collide(self):
        from core.storage.users import merge_survey_answers

        stored = merge_survey_answers({}, "ea-fc@26", {"division": "6"})
        stored = merge_survey_answers(stored, "cs2@2", {"premier_rating": "12000"})
        assert stored["ea-fc@26"] == {"division": "6"}
        assert stored["cs2@2"] == {"premier_rating": "12000"}

    def test_updating_one_game_leaves_the_other_alone(self):
        from core.storage.users import merge_survey_answers

        stored = {"ea-fc@26": {"division": "6"}, "cs2@2": {"premier_rating": "12000"}}
        out = merge_survey_answers(stored, "ea-fc@26", {"division": "4"})
        assert out["ea-fc@26"]["division"] == "4"
        assert out["cs2@2"] == {"premier_rating": "12000"}

    def test_merge_within_a_game_keeps_other_answers(self):
        """Answering one question must not wipe the rest - the account page
        saves one field at a time."""
        from core.storage.users import merge_survey_answers

        stored = {"ea-fc@26": {"division": "6", "champs_wins": "9"}}
        out = merge_survey_answers(stored, "ea-fc@26", {"division": "5"})
        assert out["ea-fc@26"] == {"division": "5", "champs_wins": "9"}

    def test_no_key_no_write(self):
        """An answer that cannot say which game it belongs to is dropped rather
        than guessed at - core has no idea what a default game would be."""
        from core.storage.users import merge_survey_answers

        stored = {"ea-fc@26": {"division": "6"}}
        assert merge_survey_answers(stored, "", {"division": "1"}) == stored
        assert merge_survey_answers(stored, None, {"division": "1"}) == stored

    def test_values_are_coerced_and_capped(self):
        from core.storage.users import merge_survey_answers

        out = merge_survey_answers({}, "ea-fc@26", {"division": 6, "note": "x" * 99,
                                                    "empty": None})
        bucket = out["ea-fc@26"]
        assert bucket["division"] == "6"
        assert len(bucket["note"]) == 32
        assert bucket["empty"] == ""

    def test_input_dict_is_not_mutated(self):
        from core.storage.users import merge_survey_answers

        stored = {"ea-fc@26": {"division": "6"}}
        merge_survey_answers(stored, "ea-fc@26", {"division": "1"})
        assert stored["ea-fc@26"]["division"] == "6"
