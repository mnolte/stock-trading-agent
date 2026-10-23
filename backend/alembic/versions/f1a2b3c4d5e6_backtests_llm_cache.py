"""backtests summary table and the shared llm_cache (PR 14).

A backtest run keeps one full live-schema database per strategy under
``data/backtests/run-<id>/``; the live database only gets the ``backtests``
summary row. ``llm_cache`` stores model answers keyed by model,
temperature, prompt version + hash and input snapshot hash, with the
cycle time, so a backtest rerun on the same inputs costs nothing
(docs/SPEC.md, Backtesting).

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model", sa.String(length=32), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("prompt_version", sa.String(length=16), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("cycle_time", sa.DateTime(), nullable=True),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "model",
            "temperature",
            "prompt_hash",
            "input_hash",
            name="uq_llm_cache_key",
        ),
    )
    op.create_index("ix_llm_cache_model", "llm_cache", ["model"])
    op.create_index("ix_llm_cache_cycle_time", "llm_cache", ["cycle_time"])

    # A decision replayed from llm_cache is free; the flag marks it.
    op.add_column(
        "decisions",
        sa.Column("cached", sa.Boolean(), nullable=False, server_default="0"),
    )

    op.create_table(
        "backtests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("universe_mode", sa.String(length=16), nullable=False),
        sa.Column("strategies", sa.JSON(), nullable=False),
        sa.Column("starting_equity", sa.Numeric(19, 8), nullable=False),
        sa.Column("slippage_pct", sa.Numeric(19, 8), nullable=False),
        sa.Column(
            "status", sa.String(length=24), nullable=False, server_default="queued"
        ),
        sa.Column("progress", sa.String(length=64), nullable=True),
        sa.Column(
            "cost_usd", sa.Numeric(19, 8), nullable=False, server_default="0"
        ),
        sa.Column("run_dir", sa.String(length=255), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("refuse_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("backtests")
    op.drop_column("decisions", "cached")
    op.drop_index("ix_llm_cache_cycle_time", "llm_cache")
    op.drop_index("ix_llm_cache_model", "llm_cache")
    op.drop_table("llm_cache")
