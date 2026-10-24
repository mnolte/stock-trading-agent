"""after-exit metrics on positions (PR 19, docs/SPEC.md, Tail metrics).

The daily after-exit job fills, for every closed position, the highest
high and the close of the ``performance.tail.after_exit_sessions`` (5)
sessions after the exit fill, split-adjusted. Values are written once and
never updated; ``after_exit_status`` runs pending → done | unavailable
(fetch failed three days in a row) and ``after_exit_done_at`` records the
write.

Revision ID: b19a0e1c2d3e
Revises: b2c3d4e5f6a7
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b19a0e1c2d3e"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "positions",
        sa.Column("after_exit_best_price", sa.Numeric(19, 8), nullable=True),
    )
    op.add_column(
        "positions",
        sa.Column("after_exit_best_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "positions",
        sa.Column("after_exit_close_price", sa.Numeric(19, 8), nullable=True),
    )
    op.add_column(
        "positions",
        sa.Column(
            "after_exit_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "positions",
        sa.Column("after_exit_done_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("positions", "after_exit_done_at")
    op.drop_column("positions", "after_exit_status")
    op.drop_column("positions", "after_exit_close_price")
    op.drop_column("positions", "after_exit_best_at")
    op.drop_column("positions", "after_exit_best_price")
