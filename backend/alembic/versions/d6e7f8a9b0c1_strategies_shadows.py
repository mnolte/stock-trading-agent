"""strategies and shadows (PR 13, docs/SPEC.md, Strategies and shadows).

Four tables: ``strategies`` (shadow configurations), ``shadow_decisions``
(one per strategy, live cycle and symbol), ``shadow_positions`` (virtual
entry orders and positions) and ``shadow_equity`` (virtual equity curve).
Live trading tables are untouched.

Revision ID: d6e7f8a9b0c1
Revises: c4d5e6f7a8b9
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(19, 8)


def upgrade() -> None:
    op.create_table(
        "strategies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("prompt_version", sa.String(length=16), nullable=True),
        sa.Column("model", sa.String(length=32), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("overrides", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("promoted_version", sa.Integer(), nullable=True),
    )
    op.create_table(
        "shadow_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("cycles.id"), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("context", sa.String(length=16), nullable=False),
        sa.Column(
            "live_decision_id",
            sa.Integer(),
            sa.ForeignKey("decisions.id"),
            nullable=True,
        ),
        sa.Column("position", sa.JSON(), nullable=True),
        sa.Column("rules_result", sa.JSON(), nullable=True),
        sa.Column("model_output", sa.JSON(), nullable=True),
        sa.Column("validation_result", sa.JSON(), nullable=True),
        sa.Column("gates_result", sa.JSON(), nullable=True),
        sa.Column("final_action", sa.String(length=32), nullable=False),
        sa.Column("no_call_reason", sa.String(length=16), nullable=True),
        sa.Column("prompt_version", sa.String(length=16), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=32), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost", MONEY, nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("missed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("strategy_id", "cycle_id", "symbol", name="uq_shadow_decisions_scs"),
    )
    op.create_index("ix_shadow_decisions_strategy_id", "shadow_decisions", ["strategy_id"])
    op.create_index("ix_shadow_decisions_cycle_id", "shadow_decisions", ["cycle_id"])
    op.create_table(
        "shadow_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "entry_decision_id",
            sa.Integer(),
            sa.ForeignKey("shadow_decisions.id"),
            nullable=True,
        ),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("limit_price", MONEY, nullable=True),
        sa.Column("entry", MONEY, nullable=True),
        sa.Column("initial_stop", MONEY, nullable=False),
        sa.Column("stop", MONEY, nullable=False),
        sa.Column("target", MONEY, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("exit_price", MONEY, nullable=True),
        sa.Column("exit_reason", sa.String(length=32), nullable=True),
        sa.Column("pnl", MONEY, nullable=True),
        sa.Column("r", MONEY, nullable=True),
        sa.Column("deferred_exit", sa.JSON(), nullable=True),
        sa.Column("exit_pending", sa.String(length=32), nullable=True),
        sa.Column("exit_requested_at", sa.DateTime(), nullable=True),
        sa.Column("bars_through", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_shadow_positions_strategy_id", "shadow_positions", ["strategy_id"])
    op.create_table(
        "shadow_equity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("time", sa.DateTime(), nullable=False),
        sa.Column("equity", MONEY, nullable=False),
        sa.Column("cash", MONEY, nullable=False),
    )
    op.create_index("ix_shadow_equity_strategy_id", "shadow_equity", ["strategy_id"])


def downgrade() -> None:
    op.drop_index("ix_shadow_equity_strategy_id", table_name="shadow_equity")
    op.drop_table("shadow_equity")
    op.drop_index("ix_shadow_positions_strategy_id", table_name="shadow_positions")
    op.drop_table("shadow_positions")
    op.drop_index("ix_shadow_decisions_cycle_id", table_name="shadow_decisions")
    op.drop_index("ix_shadow_decisions_strategy_id", table_name="shadow_decisions")
    op.drop_table("shadow_decisions")
    op.drop_table("strategies")
