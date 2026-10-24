from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6c7d8e9f0a1"
down_revision: str | None = "a15b26c37d48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "readiness_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("runs.id"),
            nullable=False,
        ),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "session_date", "run_id", name="uq_readiness_snapshots_day_run"
        ),
    )
    op.create_index(
        "ix_readiness_snapshots_session_date",
        "readiness_snapshots",
        ["session_date"],
    )
    op.create_table(
        "readiness_manual",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item", sa.String(32), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_readiness_manual_item", "readiness_manual", ["item"]
    )
    op.add_column(
        "events",
        sa.Column(
            "affects", sa.String(16), nullable=False, server_default="none"
        ),
    )
    op.add_column("events", sa.Column("resolved_at", sa.DateTime(), nullable=True))
    op.add_column(
        "events", sa.Column("resolution", sa.String(16), nullable=True)
    )
    op.add_column("events", sa.Column("note", sa.Text(), nullable=True))
    # Backfill: an event that already links to a fill, order or decision
    # affected that object; anything else affected nothing measurable.
    op.execute(
        sa.text(
            "UPDATE events SET affects = CASE "
            "WHEN json_extract(details, '$.fill_id') IS NOT NULL THEN 'fill' "
            "WHEN json_extract(details, '$.order') IS NOT NULL "
            "  OR json_extract(details, '$.order_id') IS NOT NULL "
            "  OR json_extract(details, '$.alpaca_order_id') IS NOT NULL "
            "  OR json_extract(details, '$.client_order_id') IS NOT NULL "
            "  THEN 'order' "
            "WHEN json_extract(details, '$.decision_id') IS NOT NULL "
            "  THEN 'decision' "
            "ELSE 'none' END"
        )
    )


def downgrade() -> None:
    op.drop_column("events", "note")
    op.drop_column("events", "resolution")
    op.drop_column("events", "resolved_at")
    op.drop_column("events", "affects")
    op.drop_table("readiness_manual")
    op.drop_table("readiness_snapshots")
