"""breadth and sectors: four breadth fields + sectors JSON on cycles,
market_context JSON on decisions (item 068).

All nullable: null before this item, and the breadth fields also null
whenever fewer than ``market.breadth.min_symbols`` symbols were computed.
``decisions.market_context`` holds the candidate's sector fields for
analysis — never part of the model's input.
"""

import sqlalchemy as sa
from alembic import op

revision = "u1a2b3c4d5e6"
down_revision = "v1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in (
        "breadth_above_sma50_pct",
        "breadth_above_sma200_pct",
        "breadth_ema_up_pct",
        "breadth_up_today_pct",
    ):
        op.add_column("cycles", sa.Column(name, sa.Numeric(19, 8), nullable=True))
    op.add_column("cycles", sa.Column("sectors", sa.JSON(), nullable=True))
    op.add_column("decisions", sa.Column("market_context", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("decisions", "market_context")
    op.drop_column("cycles", "sectors")
    for name in (
        "breadth_up_today_pct",
        "breadth_ema_up_pct",
        "breadth_above_sma200_pct",
        "breadth_above_sma50_pct",
    ):
        op.drop_column("cycles", name)
