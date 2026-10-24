"""after_exit_failures on positions + status index (PR 19, docs/SPEC.md,
Tail metrics).

The daily after-exit job counts fetch failures on the position row so a
process restart does not reset the retry budget; after
``performance.tail.after_exit_max_failures`` (3) failures the row is
stored ``unavailable``. The index covers the job's daily due query
(``closed_at IS NOT NULL AND after_exit_status = 'pending'``).

Revision ID: c1d2e3f4a5b6
Revises: b19a0e1c2d3e
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b19a0e1c2d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "positions",
        sa.Column(
            "after_exit_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        "ix_positions_after_exit_status",
        "positions",
        ["after_exit_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_positions_after_exit_status", table_name="positions")
    op.drop_column("positions", "after_exit_failures")
