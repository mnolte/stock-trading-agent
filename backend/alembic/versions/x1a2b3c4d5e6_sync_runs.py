"""sync_runs: one row per Universe → Data sync job run (item 077).

Written by the job itself — start, throttled progress, failures, end —
for the Data sync card; kept 30 days.
"""

import sqlalchemy as sa
from alembic import op

revision = "x1a2b3c4d5e6"
down_revision = "w1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("failures", sa.JSON(), nullable=True),
    )
    op.create_index("ix_sync_runs_job", "sync_runs", ["job"])
    op.create_index("ix_sync_runs_started_at", "sync_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_runs_started_at", table_name="sync_runs")
    op.drop_index("ix_sync_runs_job", table_name="sync_runs")
    op.drop_table("sync_runs")
