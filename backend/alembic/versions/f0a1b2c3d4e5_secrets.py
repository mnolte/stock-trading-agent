"""secrets: encrypted API-key storage (PR 10, docs/SPEC.md, API keys).

Holds the four keys the agent needs — alpaca_key_id, alpaca_secret,
openai, finnhub — Fernet-encrypted. The encryption key lives in
SA_DATA_DIR/secret.key (mode 600), never in this table. The API is
write-only: only last4, timestamps and the test result are readable.

Revision ID: f0a1b2c3d4e5
Revises: d3a4b5c6e7f8
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: str | None = "d3a4b5c6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=32), nullable=False, unique=True),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column("last4", sa.String(length=8), nullable=False),
        sa.Column("saved_at", sa.DateTime(), nullable=False),
        sa.Column("tested_at", sa.DateTime(), nullable=True),
        sa.Column("test_result", sa.String(length=32), nullable=True),
        sa.Column("test_detail", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("secrets")
