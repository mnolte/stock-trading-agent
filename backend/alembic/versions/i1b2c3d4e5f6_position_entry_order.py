"""positions.entry_order_id: dedupe position rows per broker entry order.

Root cause of the SHEL 30 Oct duplicate: the fills sync opened the row,
a stale flat reading closed it seconds later while fills settled, and
the adopt path re-created it without the decision link or the bracket
target. One row per entry order is enforced from here on; this revision
backfills the link, merges the duplicate pair into the surviving open
row and restores its target from the bracket's target leg.
"""

import sqlalchemy as sa
from alembic import op

revision = "i1b2c3d4e5f6"
down_revision = "h1a2b3c4d5e6"
branch_labels = None
depends_on = None


def _entry_order_for(
    bind: sa.engine.Connection,
    symbol: str,
    entry_decision_id: int | None,
    opened_at: str,
) -> tuple[int, str, int | None, str] | None:
    """The buy entry order a position row was opened from."""
    rows = bind.execute(
        sa.text(
            "SELECT id, alpaca_order_id, decision_id, filled_at FROM orders "
            "WHERE symbol = :s AND side = 'buy' AND leg = 'entry' "
            "AND filled_at IS NOT NULL "
            "ORDER BY filled_at DESC"
        ),
        {"s": symbol},
    ).fetchall()
    if not rows:
        return None
    before = [r for r in rows if str(r[3]) <= str(opened_at)]
    if entry_decision_id is not None:
        for r in before:
            if r[2] == entry_decision_id:
                return r
    return before[0] if before else None


def upgrade() -> None:
    with op.batch_alter_table("positions") as batch_op:
        batch_op.add_column(
            sa.Column("entry_order_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_positions_entry_order_id",
            "orders",
            ["entry_order_id"],
            ["id"],
        )
    bind = op.get_bind()
    positions = bind.execute(
        sa.text(
            "SELECT id, symbol, origin, entry_decision_id, opened_at, closed_at, "
            "target_price FROM positions ORDER BY opened_at, id"
        )
    ).fetchall()

    # 1. Backfill the entry-order link (rows may collide: that is how the
    #    duplicates are found, before the unique index exists).
    entry_of = {}
    for pid, symbol, _origin, dec_id, opened_at, _closed_at, _tgt in positions:
        order = _entry_order_for(bind, symbol, dec_id, opened_at)
        if order is not None:
            entry_of[pid] = order
            bind.execute(
                sa.text("UPDATE positions SET entry_order_id = :o WHERE id = :p"),
                {"o": order[0], "p": pid},
            )

    # 2. Merge duplicate rows that share one entry order: keep the open
    #    row (else the latest), copy the fields the survivor lacks.
    groups: dict[int, list[int]] = {}
    for pid, order in entry_of.items():
        groups.setdefault(order[0], []).append(pid)
    for order_id, pids in groups.items():
        if len(pids) < 2:
            continue
        rows = bind.execute(
            sa.text(
                "SELECT id, entry_decision_id, initial_stop, "
                "current_stop_price, target_price, opened_at, closed_at, "
                "origin FROM positions WHERE id IN :ids"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            {"ids": pids},
        ).fetchall()
        open_rows = [r for r in rows if r[6] is None]
        keep = max(open_rows or rows, key=lambda r: r[0])
        order_row = bind.execute(
            sa.text(
                "SELECT decision_id, alpaca_order_id FROM orders WHERE id = :o"
            ),
            {"o": order_id},
        ).fetchone()
        dec_id = order_row[0] if order_row else None
        if dec_id is None:
            dec_id = next((r[1] for r in rows if r[1] is not None), None)
        stop = next((r[2] for r in rows if r[2] is not None), None)
        cur_stop = next((r[3] for r in rows if r[3] is not None), None)
        target = next((r[4] for r in rows if r[4] is not None), None)
        opened = min(r[5] for r in rows)
        if target is None and order_row is not None:
            leg = bind.execute(
                sa.text(
                    "SELECT limit_price FROM orders "
                    "WHERE parent_alpaca_order_id = :p AND leg = 'target' "
                    "AND status IN ('new', 'open', 'held') LIMIT 1"
                ),
                {"p": order_row[1]},
            ).fetchone()
            if leg is not None:
                target = leg[0]
        bind.execute(
            sa.text(
                "UPDATE positions SET entry_decision_id = :d, initial_stop = :s, "
                "current_stop_price = :c, target_price = :t, opened_at = :o, "
                "origin = 'agent', pending_ack = 0 WHERE id = :p"
            ),
            {
                "d": dec_id,
                "s": stop,
                "c": cur_stop,
                "t": target,
                "o": opened,
                "p": keep[0],
            },
        )
        bind.execute(
            sa.text("DELETE FROM positions WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": [r[0] for r in rows if r[0] != keep[0]]},
        )

    # 3. Repair survivors that still miss their decision link or target.
    linked = bind.execute(
        sa.text(
            "SELECT p.id, p.entry_decision_id, p.target_price, p.origin, "
            "o.decision_id, o.alpaca_order_id FROM positions p "
            "JOIN orders o ON o.id = p.entry_order_id"
        )
    ).fetchall()
    for pid, dec_id, tgt, origin, odec, alpaca_id in linked:
        new_dec = dec_id or odec
        new_tgt = tgt
        if new_tgt is None and alpaca_id:
            leg = bind.execute(
                sa.text(
                    "SELECT limit_price FROM orders "
                    "WHERE parent_alpaca_order_id = :p AND leg = 'target' "
                    "AND status IN ('new', 'open', 'held') LIMIT 1"
                ),
                {"p": alpaca_id},
            ).fetchone()
            if leg is not None:
                new_tgt = leg[0]
        new_origin = "agent" if origin == "adopted" else origin
        bind.execute(
            sa.text(
                "UPDATE positions SET entry_decision_id = :d, "
                "target_price = :t, origin = :o WHERE id = :p"
            ),
            {"d": new_dec, "t": new_tgt, "o": new_origin, "p": pid},
        )

    op.create_index(
        "ux_positions_entry_order_id",
        "positions",
        ["entry_order_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_positions_entry_order_id", table_name="positions")
    with op.batch_alter_table("positions") as batch_op:
        batch_op.drop_column("entry_order_id")
