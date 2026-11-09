"""option touches: last fetch attempt per row (item 058).

``outcomes.option_touches_tried_at`` and
``shadow_decisions.option_touches_tried_at`` record the last session a
missing-bars fetch was tried, so a row is fetched at most once per
session instead of every cycle.
"""

import sqlalchemy as sa
from alembic import op

revision = "s1a2b3c4d5e6"
down_revision = "r1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outcomes",
        sa.Column("option_touches_tried_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "shadow_decisions",
        sa.Column("option_touches_tried_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shadow_decisions", "option_touches_tried_at")
    op.drop_column("outcomes", "option_touches_tried_at")
