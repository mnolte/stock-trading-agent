"""decisions.no_call_reason

``no_call`` is an official final action; the reason says why the model did
not decide: ``rules``, ``call_cap``, ``quote_missing`` or ``model_error``.

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-10-15 21:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decisions", sa.Column("no_call_reason", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("decisions", "no_call_reason")
