import sqlalchemy as sa
from alembic import op

revision = "e32a0b1c2d3f"
down_revision = "f31a0b1c2d3e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outcomes", sa.Column("option_touches", sa.JSON(), nullable=True))
    op.add_column("verdicts", sa.Column("thesis_check", sa.String(16), nullable=True))
    op.add_column("verdicts", sa.Column("contradiction", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("verdicts", "contradiction")
    op.drop_column("verdicts", "thesis_check")
    op.drop_column("outcomes", "option_touches")
