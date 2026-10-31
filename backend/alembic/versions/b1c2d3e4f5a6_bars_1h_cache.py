"""bars_1h + bars_1h_state: stored session-aligned 1h aggregates.

Signals read 60 sessions of 5m bars per symbol every cycle; the 1h
result of that scan changes only when the session's 5m rows do. The
state table records the fingerprint (row count, newest start) the
stored bars were computed from, so any bars_5m write — including ones
that bypass BarCache.store — recomputes on the next read.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bars_1h",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("start", sa.DateTime(), nullable=False),
        sa.Column("end", sa.DateTime(), nullable=False),
        sa.Column("open", sa.Numeric(19, 8), nullable=False),
        sa.Column("high", sa.Numeric(19, 8), nullable=False),
        sa.Column("low", sa.Numeric(19, 8), nullable=False),
        sa.Column("close", sa.Numeric(19, 8), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column("feed", sa.String(16), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "symbol", "session_date", "start", name="uq_bars_1h_sym_sess_start"
        ),
    )
    op.create_index("ix_bars_1h_symbol", "bars_1h", ["symbol"])
    op.create_table(
        "bars_1h_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("src_fingerprint", sa.String(96), nullable=False),
        sa.UniqueConstraint(
            "symbol", "session_date", name="uq_bars_1h_state_sym_sess"
        ),
    )


def downgrade() -> None:
    op.drop_table("bars_1h_state")
    op.drop_index("ix_bars_1h_symbol", table_name="bars_1h")
    op.drop_table("bars_1h")
