"""cycle duration

``cycles.duration_seconds`` stores how long each decision cycle took —
the duration warning at ``cycle_duration_warn_minutes`` reads it and
cycles are audited on it (docs/SPEC.md, Cycles at scale).

Revision ID: c4d5e6f7a8b9
Revises: a7b8c9d0e1f2
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cycles", sa.Column("duration_seconds", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("cycles", "duration_seconds")
