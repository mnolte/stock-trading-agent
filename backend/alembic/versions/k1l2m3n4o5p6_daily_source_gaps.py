"""daily_source_gaps: sessions the source confirmed it has no daily bar
for — only a successful fetch for the period writes a row, so a failed
fetch never reads as a source gap (SPEC, Adding a symbol → "Gaps at
the source").
"""

import sqlalchemy as sa
from alembic import op

revision = "k1l2m3n4o5p6"
down_revision = "j1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_source_gaps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.UniqueConstraint(
            "symbol", "session_date", name="uq_daily_gap_sym_sess"
        ),
    )


def downgrade() -> None:
    op.drop_table("daily_source_gaps")
