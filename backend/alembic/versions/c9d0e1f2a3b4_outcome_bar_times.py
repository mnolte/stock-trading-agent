"""outcome bar timestamps

PR 7: each stored outcome price keeps the timestamp of the 5-minute bar it
came from (docs/SPEC.md, Outcome tracking).

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-10-16 12:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in (
        "price_1h_at",
        "price_1session_at",
        "price_5sessions_at",
        "best_price_at",
        "worst_price_at",
    ):
        op.add_column("outcomes", sa.Column(name, sa.DateTime(), nullable=True))


def downgrade() -> None:
    for name in (
        "worst_price_at",
        "best_price_at",
        "price_5sessions_at",
        "price_1session_at",
        "price_1h_at",
    ):
        op.drop_column("outcomes", name)
