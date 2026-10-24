"""llm_calls: one row per request actually sent to OpenAI (PR 18).

Live decisions, shadow decisions and reviews each write a row in the same
transaction as their linked row; this migration creates the table with the
spec's four indexes — (session_date, sent_at), (outcome, sent_at),
(source, sent_at), (symbol, sent_at) — and backfills it from existing
decisions, shadow_decisions and reviews with the same outcome rule the
writers use (docs/SPEC.md, LLM call history).

``verdicts.facts`` stores the facts a draft-verdict call was written from,
so the inspector can rebuild the user part of that prompt like it does
for reviews.

Revision ID: b2c3d4e5f6a7
Revises: b6c7d8e9f0a1
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "b6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("decision_id", sa.Integer(), sa.ForeignKey("decisions.id"), nullable=True),
        sa.Column(
            "shadow_decision_id",
            sa.Integer(),
            sa.ForeignKey("shadow_decisions.id"),
            nullable=True,
        ),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("reviews.id"), nullable=True),
        sa.Column("verdict_id", sa.Integer(), sa.ForeignKey("verdicts.id"), nullable=True),
        sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("cycles.id"), nullable=True),
        sa.Column("symbol", sa.String(length=16), nullable=True),
        sa.Column("context", sa.String(length=16), nullable=True),
        sa.Column("model", sa.String(length=32), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("prompt_version", sa.String(length=16), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(19, 8), nullable=True),
        sa.Column("cost_source", sa.String(length=16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("final_action", sa.String(length=32), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("error_type", sa.String(length=16), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("backfilled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_llm_calls_session_sent", "llm_calls", ["session_date", "sent_at"])
    op.create_index("ix_llm_calls_outcome_sent", "llm_calls", ["outcome", "sent_at"])
    op.create_index("ix_llm_calls_source_sent", "llm_calls", ["source", "sent_at"])
    op.create_index("ix_llm_calls_symbol_sent", "llm_calls", ["symbol", "sent_at"])
    op.add_column("verdicts", sa.Column("facts", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("verdicts", "facts")
    op.drop_index("ix_llm_calls_symbol_sent", table_name="llm_calls")
    op.drop_index("ix_llm_calls_source_sent", table_name="llm_calls")
    op.drop_index("ix_llm_calls_outcome_sent", table_name="llm_calls")
    op.drop_index("ix_llm_calls_session_sent", table_name="llm_calls")
    op.drop_table("llm_calls")
