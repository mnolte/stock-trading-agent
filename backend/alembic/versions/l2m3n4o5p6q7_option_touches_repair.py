"""option_touches repair: stored v2/v3/v4 options never carried a
per-option entry, so every recorded touch is null. Clear the all-null
dicts so the outcome worker recomputes them from the decision-level
entry limit, and drop the draft verdicts written from them so the
verdict scan writes them again with the real outcomes. Confirmed
verdicts keep Marc's call and stay untouched.
"""

from alembic import op

revision = "l2m3n4o5p6q7"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None

# Draft verdicts whose facts show only null option touches were labelled
# "mixed or unknown outcomes" by the bug, not by what happened.
_STALE_DRAFTS = """
    SELECT id FROM verdicts
    WHERE source = 'draft'
      AND json_type(facts, '$.options') = 'array'
      AND json_array_length(json_extract(facts, '$.options')) > 0
      AND NOT EXISTS (
          SELECT 1
          FROM json_each(json_extract(facts, '$.options')) AS o
          WHERE json_extract(o.value, '$.touch') IS NOT NULL
      )
"""


def upgrade() -> None:
    # Keep the model-call rows (their cost counts) but drop the link.
    op.execute(
        f"UPDATE llm_calls SET verdict_id = NULL WHERE verdict_id IN ({_STALE_DRAFTS})"
    )
    op.execute(f"DELETE FROM verdicts WHERE id IN ({_STALE_DRAFTS})")
    # NULL reads as uncomputed: the next fill pass recomputes each row
    # from cached bars; a row whose bars are genuinely missing gets its
    # nulls back, which is the spec's answer too.
    op.execute(
        """
        UPDATE outcomes SET option_touches = NULL
        WHERE option_touches IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM json_each(option_touches) AS t
              WHERE t.value IS NOT NULL
          )
        """
    )


def downgrade() -> None:
    # Deleted drafts are rewritten by the verdict scan and cleared touch
    # dicts are recomputed by the outcome worker; nothing to restore.
    pass
