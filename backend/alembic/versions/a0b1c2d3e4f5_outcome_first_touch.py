"""outcome first touch

PR 9: for trades, record whether the stop or the target was touched first
(docs/SPEC.md, Outcome tracking). "stop" | "target" | "neither"; NULL until
the trade's bars were read. Existing rows stay NULL — the outcome worker
backfills them on its next pass.

Revision ID: a0b1c2d3e4f5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-17 22:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a0b1c2d3e4f5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outcomes", sa.Column("first_touch", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("outcomes", "first_touch")
