"""approvals and runs

PR 6 (GUI): ``approvals`` stores supervised-mode entries waiting for
Marc's decision; ``runs`` stores the performance run started by "Start
new run" after a paper reset (restarts benchmark, drawdown peak, losing
streak and the daily loss baseline).

Revision ID: b7c8d9e0f1a2
Revises: aa11bb22cc33
Create Date: 2026-10-16 10:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "aa11bb22cc33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("decision_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("limit_price", sa.Numeric(precision=19, scale=8), nullable=False),
        sa.Column("stop_price", sa.Numeric(precision=19, scale=8), nullable=False),
        sa.Column("target_price", sa.Numeric(precision=19, scale=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["decision_id"], ["decisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_approvals_symbol"), "approvals", ["symbol"], unique=False
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column(
            "starting_equity", sa.Numeric(precision=19, scale=8), nullable=False
        ),
        sa.Column("spy_close", sa.Numeric(precision=19, scale=8), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("runs")
    op.drop_index(op.f("ix_approvals_symbol"), table_name="approvals")
    op.drop_table("approvals")
