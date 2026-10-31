import sqlalchemy as sa
from alembic import op

revision = "g43b1c2d3e4f"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("verdicts", sa.Column("prompt_version", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("verdicts", "prompt_version")
