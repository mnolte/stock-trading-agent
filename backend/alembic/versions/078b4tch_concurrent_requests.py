"""cycles.model_seconds/max_in_flight, llm_calls.batch_id/batch_size (item 078).

``model_seconds`` is the time from the cycle's first model call sent to
the last answer; ``max_in_flight`` the most requests in flight at once
during it. ``batch_id``/``batch_size`` mark which concurrent batch a
call belonged to (docs/SPEC.md, Concurrent requests — Storage).
"""

import sqlalchemy as sa
from alembic import op

revision = "078b4tch"
down_revision = "w1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cycles",
        sa.Column("model_seconds", sa.Float(), nullable=True),
    )
    op.add_column(
        "cycles",
        sa.Column("max_in_flight", sa.Integer(), nullable=True),
    )
    op.add_column(
        "llm_calls",
        sa.Column("batch_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "llm_calls",
        sa.Column("batch_size", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_calls", "batch_size")
    op.drop_column("llm_calls", "batch_id")
    op.drop_column("cycles", "max_in_flight")
    op.drop_column("cycles", "model_seconds")
