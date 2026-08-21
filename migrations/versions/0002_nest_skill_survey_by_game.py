"""Nest users.skill_survey by "<game_id>@<edition>".

Flat answers ({"division": "6", ...}) collide the moment a second game ships a
survey, so the stored shape becomes {"ea-fc@26": {"division": "6", ...}}. Every
flat dict written before this migration is FC 26 data - it is the only game
that has ever had survey questions - so the backfill wraps them under that key.

Pure data migration: no DDL, the column stays JSONB. Values in a flat dict are
always strings (update_user coerces them), values in a nested dict are always
objects - which is what makes flat rows detectable, and the migration safe to
re-run.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE users
        SET skill_survey = jsonb_build_object('ea-fc@26', skill_survey)
        WHERE skill_survey IS NOT NULL
          AND skill_survey <> '{}'::jsonb
          AND NOT EXISTS (
            SELECT 1 FROM jsonb_each(skill_survey) AS kv(k, v)
            WHERE jsonb_typeof(kv.v) = 'object'
          )
        """
    )


def downgrade() -> None:
    # Un-nest FC's answers back to the top level; any other game's answers have
    # nowhere to live in the flat shape and are dropped with it.
    op.execute(
        """
        UPDATE users
        SET skill_survey = COALESCE(skill_survey -> 'ea-fc@26', '{}'::jsonb)
        WHERE EXISTS (
            SELECT 1 FROM jsonb_each(skill_survey) AS kv(k, v)
            WHERE jsonb_typeof(kv.v) = 'object'
        )
        """
    )
