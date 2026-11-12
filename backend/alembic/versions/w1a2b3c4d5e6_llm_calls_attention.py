"""llm_calls.attention: none | watch | act (item 074).

Set once at write time by ``app.inspector.attention`` and never
recomputed; rows that existed before the column backfill to ``none``.
"""

import sqlalchemy as sa
from alembic import op

revision = "w1a2b3c4d5e6"
down_revision = "u1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_calls",
        sa.Column(
            "attention",
            sa.String(length=8),
            nullable=False,
            server_default="none",
        ),
    )
    op.create_index("ix_llm_calls_attention", "llm_calls", ["attention"])
    op.create_index(
        "ix_llm_calls_reasons_meta",
        "llm_calls",
        ["reasons", "attention", "kind"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_calls_reasons_meta", table_name="llm_calls")
    op.drop_index("ix_llm_calls_attention", table_name="llm_calls")
    op.drop_column("llm_calls", "attention")
