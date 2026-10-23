from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a15b26c37d48"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("initial_stop", sa.Numeric(20, 8), nullable=True))
    op.add_column("orders", sa.Column("active_until", sa.DateTime(), nullable=True))
    op.execute(sa.text(
        "UPDATE positions SET initial_stop = ("
        "SELECT CAST(json_extract(model_output, '$.stop_price') AS NUMERIC) "
        "FROM decisions WHERE decisions.id = positions.entry_decision_id)"
    ))
    for table in ("decisions", "shadow_decisions", "reviews", "verdicts"):
        op.add_column(table, sa.Column(
            "cost_source", sa.String(16), nullable=False, server_default="priced",
        ))
        cached = "WHEN cached = 1 THEN 'cached' " if table == "decisions" else ""
        op.execute(sa.text(
            f"UPDATE {table} SET cost_source = CASE {cached}"
            "WHEN cost > 0 THEN 'priced' ELSE 'legacy' END"
        ))


def downgrade() -> None:
    for table in ("verdicts", "reviews", "shadow_decisions", "decisions"):
        op.drop_column(table, "cost_source")
    op.drop_column("orders", "active_until")
    op.drop_column("positions", "initial_stop")
