"""shadow_decisions: missed reason and shadow-only snapshots (PR 13 review).

``missed_reason`` says why shadow work was skipped: ``time`` (the next
live cycle or position check was due) or ``cost_cap``. ``input_snapshot``
holds the snapshot live's cycle built for a symbol only shadows hold —
live makes no decision for it, so there is no live row to share.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "shadow_decisions", sa.Column("missed_reason", sa.String(length=16), nullable=True)
    )
    op.add_column("shadow_decisions", sa.Column("input_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("shadow_decisions", "input_snapshot")
    op.drop_column("shadow_decisions", "missed_reason")
