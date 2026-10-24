"""log_reviews: reviewed recurring log problems (PR 20, docs/SPEC.md,
Readiness — Errors in the log).

Revision ID: d4e5f6a7b8c9
Revises: b3c4d5e6f7a8
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "log_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(length=400), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("sample", sa.Text(), nullable=False),
        sa.Column("review", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.Column("app_version", sa.String(length=32), nullable=False),
    )
    op.create_index("ix_log_reviews_fingerprint", "log_reviews", ["fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_log_reviews_fingerprint", table_name="log_reviews")
    op.drop_table("log_reviews")
