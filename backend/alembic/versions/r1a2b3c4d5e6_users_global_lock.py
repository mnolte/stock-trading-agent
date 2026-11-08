"""Persist the global sign-in pause on the user (issue #161)."""

import sqlalchemy as sa
from alembic import op

revision = "r1a2b3c4d5e6"
down_revision = "q1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("global_locked_until", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "global_locked_until")
