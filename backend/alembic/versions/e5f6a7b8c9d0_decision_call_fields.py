"""decision call fields and cycles.kind

Adds the model-call fields the LLM inspector needs (raw response, latency,
token split) to ``decisions``, and ``kind`` (cycle | position check) to
``cycles`` per the spec data model.

Revision ID: e5f6a7b8c9d0
Revises: a1b2c3d4e5f6
Create Date: 2026-10-15 20:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cycles",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="cycle"),
    )
    op.add_column("decisions", sa.Column("tokens_in", sa.Integer(), nullable=True))
    op.add_column("decisions", sa.Column("tokens_out", sa.Integer(), nullable=True))
    op.add_column("decisions", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column("decisions", sa.Column("raw_response", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("decisions", "raw_response")
    op.drop_column("decisions", "latency_ms")
    op.drop_column("decisions", "tokens_out")
    op.drop_column("decisions", "tokens_in")
    op.drop_column("cycles", "kind")
