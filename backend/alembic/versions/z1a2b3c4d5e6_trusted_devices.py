"""Trusted devices and the session→device link (item 083)."""

import sqlalchemy as sa
from alembic import op

revision = "z1a2b3c4d5e6"
# Merge point: this branch already carries two heads (the bars cache and
# the auth work); the trusted-devices schema sits on top of both.
down_revision = ("078b4tch", "b1c2d3e4f5a6")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trusted_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("device_name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("failures", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_trusted_devices_token_hash", "trusted_devices", ["token_hash"], unique=True
    )
    # SQLite cannot ALTER a column with a foreign key in — batch mode
    # recreates the table, and the FK gets an explicit name.
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("trusted_device_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_sessions_trusted_device_id",
            "trusted_devices",
            ["trusted_device_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_constraint("fk_sessions_trusted_device_id", type_="foreignkey")
        batch.drop_column("trusted_device_id")
    op.drop_index("ix_trusted_devices_token_hash", table_name="trusted_devices")
    op.drop_table("trusted_devices")
