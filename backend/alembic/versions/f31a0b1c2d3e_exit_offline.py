import sqlalchemy as sa
from alembic import op

revision = "f31a0b1c2d3e"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "positions",
        sa.Column(
            "closed_while_offline",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "positions",
        sa.Column("exit_filled_qty", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("positions", "exit_filled_qty")
    op.drop_column("positions", "closed_while_offline")
