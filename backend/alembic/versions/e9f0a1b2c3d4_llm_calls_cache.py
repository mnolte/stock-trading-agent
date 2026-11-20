"""llm_calls prompt-cache columns (item 109).

``tokens_cached_in`` (input read from the provider's prompt cache),
``tokens_cache_write_in`` (Anthropic cache creation) and ``cache_saved``
(the money the cache saved at the call's own prices) join the stored
token counts so Insights → Model costs can show cached prompts
(docs/SPEC.md → LLM call history → Storage, Insights → Model costs).
Rows written before the columns stay NULL — never rewritten.
"""

import sqlalchemy as sa
from alembic import op

revision = "e9f0a1b2c3d4"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_calls", sa.Column("tokens_cached_in", sa.Integer(), nullable=True)
    )
    op.add_column(
        "llm_calls",
        sa.Column("tokens_cache_write_in", sa.Integer(), nullable=True),
    )
    op.add_column(
        "llm_calls",
        sa.Column("cache_saved", sa.Numeric(19, 8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_calls", "cache_saved")
    op.drop_column("llm_calls", "tokens_cache_write_in")
    op.drop_column("llm_calls", "tokens_cached_in")
