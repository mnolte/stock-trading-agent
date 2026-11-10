"""vix data: vix_closes table + the seven VIX fields on cycles (item 060).

``vix_closes`` stores FRED VIXCLS daily closes; ``cycles`` gains the
per-cycle VIX snapshot (close, date, percentile, 5-session change, the
two VIXY moves and the stale flag) — all nullable, all null before this
item and whenever FRED has no usable close.
"""

import sqlalchemy as sa
from alembic import op

revision = "t1a2b3c4d5e6"
down_revision = "s1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vix_closes",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("close", sa.Numeric(19, 8), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )
    for name, col_type in (
        ("vix_close", sa.Numeric(19, 8)),
        ("vix_date", sa.Date()),
        ("vix_percentile_1y", sa.Numeric(19, 8)),
        ("vix_change_5d_pct", sa.Numeric(19, 8)),
        ("vixy_change_today_pct", sa.Numeric(19, 8)),
        ("vixy_change_since_vix_pct", sa.Numeric(19, 8)),
        ("vix_stale", sa.Boolean()),
    ):
        op.add_column("cycles", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    for name in (
        "vix_stale",
        "vixy_change_since_vix_pct",
        "vixy_change_today_pct",
        "vix_change_5d_pct",
        "vix_percentile_1y",
        "vix_date",
        "vix_close",
    ):
        op.drop_column("cycles", name)
    op.drop_table("vix_closes")
