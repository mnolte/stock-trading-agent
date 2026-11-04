"""llm_calls.sent_input: the input snapshot exactly as sent to the model.

The inspector's "User · input snapshot" showed the stored snapshot, but
v3/v4 calls send a stripped copy (no indicators_precise, no capped
levels). From now on the send copy is stored on the call row; rows
written before this change are rebuilt on read with the same strip
function (docs/SPEC.md, Fixes of 3 November).

Revision ID: n1b2c3d4e5f6
Revises: m1b2c3d4e5f6
"""

import sqlalchemy as sa
from alembic import op

revision = "n1b2c3d4e5f6"
down_revision = "m1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_calls", sa.Column("sent_input", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_calls", "sent_input")
