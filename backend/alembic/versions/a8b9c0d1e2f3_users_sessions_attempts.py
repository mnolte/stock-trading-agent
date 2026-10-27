"""users, sessions and sign_in_attempts (PR 23, docs/SPEC.md, Sign-in).

One ``users`` row created by ``make create-user``; ``sessions`` holds
per-device sign-ins (token hash only — the token itself is never
stored); ``sign_in_attempts`` feeds the per-address and global rate
limits and is pruned after 90 days.

Revision ID: a8b9c0d1e2f3
Revises: e8f9a0b1c2d3
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("totp_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("recovery_code_hashes", sa.JSON(), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(length=128), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("keep_signed_in", sa.Boolean(), nullable=False),
        sa.Column("confirmed_until", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"])
    op.create_table(
        "sign_in_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("time", sa.DateTime(), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("result", sa.String(length=8), nullable=False),
    )
    op.create_index("ix_sign_in_attempts_time", "sign_in_attempts", ["time"])
    op.create_index("ix_sign_in_attempts_ip", "sign_in_attempts", ["ip"])


def downgrade() -> None:
    op.drop_table("sign_in_attempts")
    op.drop_table("sessions")
    op.drop_table("users")
