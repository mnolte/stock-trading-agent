"""Drop llm_calls rows for calls that were never sent.

The PR-18 backfill selected decisions with ``model_output IS NOT NULL``,
but SQLAlchemy's JSON type wrote Python ``None`` as the JSON text
``'null'``, so every rules/call-cap/price no_call decision matched
"has a model output". ``_decision_call`` then classified them
``invalid_output`` because a no_call row's validation result is never
valid. Per docs/SPEC.md (LLM call history — What counts as a call),
nothing that wasn't sent gets a row, so the phantom rows are deleted.

The same ``'null'`` text is normalised to SQL NULL on every nullable
JSON column so ``IS NOT NULL`` means "has a value" again; the mapped
columns now use ``JSON(none_as_null=True)`` so new rows write NULL.

Revision ID: b3c4d5e6f7a8
Revises: c1d2e3f4a5b6
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every nullable JSON column; the ORM wrote 'null' for Python None.
_JSON_NULL_COLUMNS: dict[str, tuple[str, ...]] = {
    "cycles": ("errors", "data_source_health"),
    "decisions": (
        "input_snapshot",
        "rules_result",
        "model_output",
        "validation_result",
        "gates_result",
    ),
    "positions": ("deferred_exit",),
    "universe_screens": ("check_results",),
    "reviews": ("facts", "content"),
    "verdicts": ("facts",),
    "events": ("details",),
    "strategies": ("overrides",),
    "shadow_decisions": (
        "position",
        "rules_result",
        "model_output",
        "validation_result",
        "gates_result",
        "input_snapshot",
    ),
    "shadow_positions": ("deferred_exit",),
    "backtests": ("warnings", "summary"),
}


def upgrade() -> None:
    conn = op.get_bind()
    # A no_call_reason other than "model_error" means the request was
    # never sent; kind = 'decision' leaves review and verdict calls alone
    # (a verdict row also carries its decision's id).
    conn.execute(
        sa.text(
            "DELETE FROM llm_calls"
            " WHERE kind = 'decision'"
            "   AND verdict_id IS NULL"
            "   AND review_id IS NULL"
            "   AND (decision_id IN (SELECT id FROM decisions"
            "                         WHERE no_call_reason IS NOT NULL"
            "                           AND no_call_reason <> 'model_error')"
            "     OR shadow_decision_id IN (SELECT id FROM shadow_decisions"
            "                                WHERE no_call_reason IS NOT NULL"
            "                                  AND no_call_reason <> 'model_error'))"
        )
    )
    for table, columns in _JSON_NULL_COLUMNS.items():
        for column in columns:
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET {column} = NULL WHERE {column} = 'null'"
                )
            )


def downgrade() -> None:
    # The deleted rows misrepresented calls that never happened and the
    # 'null' text they relied on is not worth restoring.
    pass
