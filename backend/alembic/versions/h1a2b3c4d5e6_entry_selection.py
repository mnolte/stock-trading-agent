import sqlalchemy as sa
from alembic import op

revision = "h1a2b3c4d5e6"
down_revision = "g43b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cycles", sa.Column("selection", sa.JSON(), nullable=True))
    op.add_column("decisions", sa.Column("selection", sa.JSON(), nullable=True))
    op.add_column(
        "shadow_decisions", sa.Column("selection", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("shadow_decisions", "selection")
    op.drop_column("decisions", "selection")
    op.drop_column("cycles", "selection")
