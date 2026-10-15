"""earnings hour_raw and normalised time_of_day

Revision ID: d4e7f2a91b3c
Revises: fb346e483880
Create Date: 2026-10-15 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e7f2a91b3c"
down_revision: str | None = "fb346e483880"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("earnings") as batch_op:
        batch_op.add_column(sa.Column("hour_raw", sa.String(length=16), nullable=True))
    # Existing rows kept Finnhub's raw value in time_of_day; move it to
    # hour_raw, then normalise time_of_day to the new vocabulary.
    op.execute("UPDATE earnings SET hour_raw = time_of_day")
    op.execute(
        "UPDATE earnings SET time_of_day = CASE lower(time_of_day) "
        "WHEN 'bmo' THEN 'before_open' "
        "WHEN 'amc' THEN 'after_close' "
        "WHEN 'dmh' THEN 'during_market' "
        "ELSE 'unknown' END"
    )
    with op.batch_alter_table("earnings") as batch_op:
        batch_op.alter_column(
            "hour_raw", existing_type=sa.String(length=16), nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("earnings") as batch_op:
        batch_op.drop_column("hour_raw")
