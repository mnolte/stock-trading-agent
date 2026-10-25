"""decisions + shadow_decisions: decision.v2 levels/options columns (PR 21).

Under decision.v2 the snapshot carries code-built ``levels`` and
``options`` (or ``stop_options`` for held positions); the model picks an
option id and writes no prices. ``chosen_option`` is that id,
``explanation_check`` holds the stored check result, ``options_version``
marks the builder format. All NULL for v1 rows.

Revision ID: c3a9f1e2b7d4
Revises: d4e5f6a7b8c9
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3a9f1e2b7d4"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("levels", sa.JSON()),
    ("options", sa.JSON()),
    ("chosen_option", sa.String(length=8)),
    ("explanation_check", sa.JSON()),
    ("options_version", sa.String(length=16)),
)


def upgrade() -> None:
    for table in ("decisions", "shadow_decisions"):
        for name, col_type in _COLUMNS:
            op.add_column(table, sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    for table in ("decisions", "shadow_decisions"):
        for name, _ in _COLUMNS:
            op.drop_column(table, name)
