"""universe_pool and universe_screens (PR 11, docs/SPEC.md, Larger universe).

The screened-mode pool and the per-session screen results behind it. Both
tables are empty until ``universe.mode`` is switched to ``screened`` — the
default stays ``fixed``, so v0.1 behaviour is unchanged.

Revision ID: e6f7a8b9c0d1
Revises: f0a1b2c3d4e5
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "universe_pool",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=16), nullable=False, unique=True),
        sa.Column("sector", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "universe_screens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("check_results", sa.JSON(), nullable=True),
        sa.Column("last_close", sa.Numeric(19, 8), nullable=True),
        sa.Column("avg_dollar_volume", sa.Numeric(19, 8), nullable=True),
        sa.Column("return_vs_spy", sa.Numeric(19, 8), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("failed_in_a_row", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "session_date", "symbol", name="uq_universe_screens_day_symbol"
        ),
    )
    op.create_index(
        "ix_universe_screens_session_date", "universe_screens", ["session_date"]
    )
    op.create_index("ix_universe_screens_symbol", "universe_screens", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_universe_screens_symbol", table_name="universe_screens")
    op.drop_index(
        "ix_universe_screens_session_date", table_name="universe_screens"
    )
    op.drop_table("universe_screens")
    op.drop_table("universe_pool")
