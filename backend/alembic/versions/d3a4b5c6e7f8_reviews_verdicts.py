"""reviews and verdicts

PR 9: the weekly review, daily briefing and decision verdicts
(docs/SPEC.md, Reviews). reviews: one row per written weekly review or
daily briefing, keyed by kind + period so a missed run written at the
next start cannot duplicate. verdicts: one draft per eligible decision
once its 5-session outcome is in; Marc confirms or changes it.

Revision ID: d3a4b5c6e7f8
Revises: a0b1c2d3e4f5
Create Date: 2026-10-18 10:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3a4b5c6e7f8"
down_revision: str | None = "a0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=32), nullable=True),
        sa.Column("prompt_version", sa.String(length=16), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(precision=19, scale=8), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "period", name="uq_reviews_kind_period"),
    )
    op.create_table(
        "verdicts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("decision_id", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("draft_source", sa.String(length=16), nullable=False),
        sa.Column("cost", sa.Numeric(precision=19, scale=8), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["decisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id"),
    )


def downgrade() -> None:
    op.drop_table("verdicts")
    op.drop_table("reviews")
