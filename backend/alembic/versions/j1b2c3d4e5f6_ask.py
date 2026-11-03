"""Ask the agent (PR 36): ask_threads, ask_messages, ask_notes and the
llm_calls link to the answered message.

ask_messages keeps steps, references, unverified numbers and the checked
numbers (tool results themselves are not stored); ask_notes survives its
source chat being cleaned up (the link goes NULL).
"""

import sqlalchemy as sa
from alembic import op

revision = "j1b2c3d4e5f6"
down_revision = "i1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ask_threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(64), nullable=False),
        sa.Column("context", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.create_table(
        "ask_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "thread_id",
            sa.Integer(),
            sa.ForeignKey("ask_threads.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("steps", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("references", sa.JSON(none_as_null=True), nullable=True),
        sa.Column(
            "unverified_numbers", sa.JSON(none_as_null=True), nullable=True
        ),
        sa.Column("checked_numbers", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("model", sa.String(32), nullable=True),
        sa.Column("prompt_version", sa.String(16), nullable=True),
        sa.Column("prompt_hash", sa.String(64), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ask_messages_thread_id", "ask_messages", ["thread_id"])
    op.create_table(
        "ask_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("references", sa.JSON(none_as_null=True), nullable=True),
        sa.Column(
            "source_thread_id",
            sa.Integer(),
            sa.ForeignKey("ask_threads.id"),
            nullable=True,
        ),
        sa.Column(
            "source_message_id",
            sa.Integer(),
            sa.ForeignKey("ask_messages.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("llm_calls") as batch_op:
        batch_op.add_column(
            sa.Column("ask_message_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_llm_calls_ask_message_id",
            "ask_messages",
            ["ask_message_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_calls") as batch_op:
        batch_op.drop_constraint("fk_llm_calls_ask_message_id", type_="foreignkey")
        batch_op.drop_column("ask_message_id")
    op.drop_table("ask_notes")
    op.drop_index("ix_ask_messages_thread_id", table_name="ask_messages")
    op.drop_table("ask_messages")
    op.drop_table("ask_threads")
