"""bars_1d: daily SIP bars for daily indicators

Revision ID: a1b2c3d4e5f6
Revises: d4e7f2a91b3c
Create Date: 2026-10-15 17:30:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "d4e7f2a91b3c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bars_1d",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=19, scale=8), nullable=False),
        sa.Column("high", sa.Numeric(precision=19, scale=8), nullable=False),
        sa.Column("low", sa.Numeric(precision=19, scale=8), nullable=False),
        sa.Column("close", sa.Numeric(precision=19, scale=8), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "date", name="uq_bars_1d_symbol_date"),
    )
    op.create_index(op.f("ix_bars_1d_symbol"), "bars_1d", ["symbol"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_bars_1d_symbol"), table_name="bars_1d")
    op.drop_table("bars_1d")
