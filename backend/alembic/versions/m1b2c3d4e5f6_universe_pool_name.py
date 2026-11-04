"""universe_pool.name: the cleaned company name per pool symbol (PR —
fixes of 3 November).

Filled from Alpaca's asset name when a symbol is added and at each daily
screen — existing rows are backfilled by the next screen, never inside a
migration (no network there). ``about_symbol`` matches headlines against
it, with ``universe.names`` overriding.

Revision ID: m1b2c3d4e5f6
Revises: l2m3n4o5p6q7
"""

import sqlalchemy as sa
from alembic import op

revision = "m1b2c3d4e5f6"
down_revision = "l2m3n4o5p6q7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("universe_pool", sa.Column("name", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("universe_pool", "name")
