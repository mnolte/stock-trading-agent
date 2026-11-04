"""shadow_decisions.option_touches: counterfactual touches per option.

The counterfactual touches (docs/SPEC.md, Decision v5) measure what
each filter removed — options offered, removed or built but never
offered. Live decisions store them on outcomes.option_touches; shadow
decisions get their own column filled by the same outcome job.

Revision ID: o1a2b3c4d5e6
Revises: n1b2c3d4e5f6
"""

import sqlalchemy as sa
from alembic import op

revision = "o1a2b3c4d5e6"
down_revision = "n1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shadow_decisions", sa.Column("option_touches", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("shadow_decisions", "option_touches")
