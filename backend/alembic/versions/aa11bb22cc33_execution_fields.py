"""execution fields

PR 5 (Execution): orders get ``symbol`` and ``client_order_id`` so a
timed-out order call can be found by reconciliation instead of resent;
positions get ``origin`` (agent | adopted), ``pending_ack`` and
``deferred_exit`` for the adoption flow and the day-trade deferral.

Revision ID: aa11bb22cc33
Revises: f7a8b9c0d1e2
Create Date: 2026-10-15 22:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aa11bb22cc33"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("symbol", sa.String(length=16), nullable=True))
    op.add_column(
        "orders", sa.Column("client_order_id", sa.String(length=64), nullable=True)
    )
    op.create_index(
        op.f("ix_orders_client_order_id"), "orders", ["client_order_id"], unique=False
    )
    op.execute("UPDATE orders SET symbol = '' WHERE symbol IS NULL")
    with op.batch_alter_table("orders") as batch:
        batch.alter_column("symbol", nullable=False)

    op.add_column(
        "positions",
        sa.Column(
            "origin",
            sa.String(length=16),
            nullable=False,
            server_default="agent",
        ),
    )
    op.add_column(
        "positions",
        sa.Column("pending_ack", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column("positions", sa.Column("deferred_exit", sa.JSON(), nullable=True))
    op.add_column("events", sa.Column("acknowledged_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "acknowledged_at")
    op.drop_column("positions", "deferred_exit")
    op.drop_column("positions", "pending_ack")
    op.drop_column("positions", "origin")
    with op.batch_alter_table("orders") as batch:
        batch.alter_column("symbol", nullable=True)
    op.drop_index(op.f("ix_orders_client_order_id"), table_name="orders")
    op.drop_column("orders", "client_order_id")
    op.drop_column("orders", "symbol")
