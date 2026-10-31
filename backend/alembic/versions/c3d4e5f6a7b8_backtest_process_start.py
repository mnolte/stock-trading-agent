"""backtests.process_started_at: the child's start time (scan fix).

A recorded pid alone can't prove a process is ours — the OS recycles
ids. The start time pins the exact process so a signal never lands on
a reused pid.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b1c2d3e4f5a6"


def upgrade() -> None:
    op.add_column(
        "backtests",
        sa.Column("process_started_at", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("backtests", "process_started_at")
