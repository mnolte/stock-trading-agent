"""economic releases: economic_events table + release columns (item 067).

``economic_events`` stores the market-wide release calendar — FOMC dates
from config, CPI/jobs/PCE dates from FRED. ``cycles.next_release`` holds
the next release within the configured sessions; ``decisions`` and
``positions`` carry the ``release_next_morning`` tag for entries and
positions held into an 08:30 release.
"""

import sqlalchemy as sa
from alembic import op

revision = "v1a2b3c4d5e6"
down_revision = "t1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "economic_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("release_type", sa.String(length=16), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("time", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("release_type", "date", name="uq_economic_events_type_date"),
    )
    op.create_index("ix_economic_events_release_type", "economic_events", ["release_type"])
    op.create_index("ix_economic_events_date", "economic_events", ["date"])
    op.add_column("cycles", sa.Column("next_release", sa.JSON(), nullable=True))
    op.add_column("decisions", sa.Column("release_next_morning", sa.JSON(), nullable=True))
    op.add_column("positions", sa.Column("release_dates", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("positions", "release_dates")
    op.drop_column("decisions", "release_next_morning")
    op.drop_column("cycles", "next_release")
    op.drop_index("ix_economic_events_date", table_name="economic_events")
    op.drop_index("ix_economic_events_release_type", table_name="economic_events")
    op.drop_table("economic_events")
