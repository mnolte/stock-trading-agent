"""llm_calls provider + provider_request_id; llm_cache provider + effort.

PR 29 (docs/SPEC.md → Model providers): every call row records its
provider and the provider's request id — the old request_id column
stays and its values are copied across. The answer cache key gains
provider and effort (Claude models have no temperature 0).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f7"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_calls", sa.Column("provider", sa.String(16), nullable=True))
    op.add_column(
        "llm_calls", sa.Column("provider_request_id", sa.String(64), nullable=True)
    )
    # Every existing call was an OpenAI call.
    op.execute(
        "UPDATE llm_calls SET provider = 'openai', "
        "provider_request_id = request_id"
    )
    op.add_column(
        "llm_cache",
        sa.Column("provider", sa.String(16), nullable=False, server_default="openai"),
    )
    op.add_column("llm_cache", sa.Column("effort", sa.String(16), nullable=True))
    with op.batch_alter_table("llm_cache") as batch:
        batch.drop_constraint("uq_llm_cache_key", type_="unique")
        batch.create_unique_constraint(
            "uq_llm_cache_key",
            ["provider", "model", "effort", "temperature", "prompt_hash",
             "input_hash"],
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_cache") as batch:
        batch.drop_constraint("uq_llm_cache_key", type_="unique")
        batch.create_unique_constraint(
            "uq_llm_cache_key",
            ["model", "temperature", "prompt_hash", "input_hash"],
        )
    op.drop_column("llm_cache", "effort")
    op.drop_column("llm_cache", "provider")
    op.drop_column("llm_calls", "provider_request_id")
    op.drop_column("llm_calls", "provider")
