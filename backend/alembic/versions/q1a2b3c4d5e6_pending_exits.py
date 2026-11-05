"""Persist live pending exits (issue #136)."""

import sqlalchemy as sa
from alembic import op

revision = "q1a2b3c4d5e6"
down_revision = "p1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("pending_exit", sa.JSON(none_as_null=True), nullable=True))


def downgrade() -> None:
    op.drop_column("positions", "pending_exit")
