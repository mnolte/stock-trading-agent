"""positions.entry_order_id: relink rows the first backfill missed.

PG (position 8, 30 Oct) kept ``entry_order_id`` NULL: its ``opened_at``
is the order-submit time, before the entry ``filled_at``, so the
i1b2c3d4e5f6 match (``filled_at <= opened_at``) skipped it. While open
that broke the protective-stop coverage math, which reads the entry
order's qty through this link. The decision link is unambiguous — an
entry order carries the position's ``entry_decision_id`` — so backfill
through it. Adopted rows have no entry decision and stay NULL.
"""

import sqlalchemy as sa
from alembic import op

revision = "a2b3c4d5e6f7"
down_revision = "z1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE positions SET entry_order_id = ("
            "  SELECT o.id FROM orders o"
            "  WHERE o.decision_id = positions.entry_decision_id"
            "    AND o.side = 'buy' AND o.leg = 'entry'"
            "  ORDER BY (o.status = 'filled') DESC, o.id DESC LIMIT 1"
            ")"
            " WHERE entry_order_id IS NULL"
            "   AND entry_decision_id IS NOT NULL"
            "   AND EXISTS ("
            "     SELECT 1 FROM orders o"
            "     WHERE o.decision_id = positions.entry_decision_id"
            "       AND o.side = 'buy' AND o.leg = 'entry'"
            "   )"
        )
    )


def downgrade() -> None:
    # The relinked rows are correct; nothing to restore.
    pass
