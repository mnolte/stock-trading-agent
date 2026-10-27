"""insights_snapshots (PR 22)

Revision ID: e8f9a0b1c2d3
Revises: c3a9f1e2b7d4
Create Date: 2026-10-27

Daily insights snapshot per strategy and period: session date, strategy
(NULL = live), period (run | last20 | all), computed at, and the JSON
with headline numbers, funnel, findings, slices and calibration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: str | None = "c3a9f1e2b7d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insights_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column(
            "strategy_id",
            sa.Integer(),
            sa.ForeignKey("strategies.id"),
            nullable=True,
        ),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "session_date", "strategy_id", "period", name="uq_insights_snap"
        ),
    )
    op.create_index(
        "ix_insights_snapshots_session_date",
        "insights_snapshots",
        ["session_date"],
    )
    op.create_index(
        "ix_insights_snapshots_strategy_id",
        "insights_snapshots",
        ["strategy_id"],
    )


def downgrade() -> None:
    op.drop_table("insights_snapshots")
