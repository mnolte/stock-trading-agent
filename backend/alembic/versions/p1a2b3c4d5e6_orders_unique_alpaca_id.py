"""orders.alpaca_order_id: unique index.

Every broker order id maps to exactly one row. The column was only
indexed, not unique, so two writers on the shared loop (the submit path
vs reconcile or a trade update) could each select-then-insert the same
order and grow a duplicate — after which ``scalar_one_or_none()`` raised
MultipleResultsFound on every reconcile pass until the rows were cleaned
by hand. The revision first merges any existing duplicates into the
oldest row (fills and positions are repointed, the later rows removed)
and then swaps the plain index for a unique one.

Revision ID: p1a2b3c4d5e6
Revises: o1a2b3c4d5e6
"""

import sqlalchemy as sa
from alembic import op

revision = "p1a2b3c4d5e6"
down_revision = "o1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Merge duplicates into the oldest row per alpaca_order_id.
    dupes = conn.execute(
        sa.text(
            "SELECT alpaca_order_id, MIN(id) FROM orders"
            " GROUP BY alpaca_order_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    for alpaca_id, keep_id in dupes:
        dupe_ids = [
            r[0]
            for r in conn.execute(
                sa.text(
                    "SELECT id FROM orders WHERE alpaca_order_id = :aid"
                    " AND id != :keep"
                ),
                {"aid": alpaca_id, "keep": keep_id},
            )
        ]
        for dupe_id in dupe_ids:
            conn.execute(
                sa.text("UPDATE fills SET order_id = :keep WHERE order_id = :dupe"),
                {"keep": keep_id, "dupe": dupe_id},
            )
            conn.execute(
                sa.text(
                    "UPDATE positions SET entry_order_id = :keep"
                    " WHERE entry_order_id = :dupe"
                ),
                {"keep": keep_id, "dupe": dupe_id},
            )
            conn.execute(
                sa.text("DELETE FROM orders WHERE id = :dupe"),
                {"dupe": dupe_id},
            )
    op.drop_index("ix_orders_alpaca_order_id", table_name="orders")
    op.create_index(
        "ix_orders_alpaca_order_id", "orders", ["alpaca_order_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_orders_alpaca_order_id", table_name="orders")
    op.create_index(
        "ix_orders_alpaca_order_id", "orders", ["alpaca_order_id"], unique=False
    )
