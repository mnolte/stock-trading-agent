from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Money


class Bar5m(Base):
    """5-minute SIP bars, cached locally."""

    __tablename__ = "bars_5m"
    __table_args__ = (UniqueConstraint("symbol", "start", name="uq_bars_5m_symbol_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    start: Mapped[datetime] = mapped_column(nullable=False)
    open: Mapped[Decimal] = mapped_column(Money, nullable=False)
    high: Mapped[Decimal] = mapped_column(Money, nullable=False)
    low: Mapped[Decimal] = mapped_column(Money, nullable=False)
    close: Mapped[Decimal] = mapped_column(Money, nullable=False)
    volume: Mapped[int] = mapped_column(nullable=False)
    feed: Mapped[str] = mapped_column(String(16), nullable=False)


class Bar1d(Base):
    """Daily SIP bars (one per trading session), cached locally."""

    __tablename__ = "bars_1d"
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_bars_1d_symbol_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    date: Mapped[date] = mapped_column(nullable=False)
    open: Mapped[Decimal] = mapped_column(Money, nullable=False)
    high: Mapped[Decimal] = mapped_column(Money, nullable=False)
    low: Mapped[Decimal] = mapped_column(Money, nullable=False)
    close: Mapped[Decimal] = mapped_column(Money, nullable=False)
    volume: Mapped[int] = mapped_column(nullable=False)


class Cycle(Base):
    """One decision cycle or position check."""

    __tablename__ = "cycles"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="cycle")
    scheduled_time: Mapped[datetime] = mapped_column(nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    errors: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    data_source_health: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)


class Decision(Base):
    """One model decision, including skips."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    context: Mapped[str] = mapped_column(String(16), nullable=False)  # candidate | held
    input_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    rules_result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    model_output: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    validation_result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    gates_result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    final_action: Mapped[str] = mapped_column(String(32), nullable=False)
    # Why no model answer counts: rules | call_cap | quote_missing | model_error
    no_call_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(32), nullable=False)
    tokens: Mapped[int | None] = mapped_column(nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)


class Order(Base):
    """An order leg at Alpaca."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    alpaca_order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Orders carry our client id from before submit, so a timed-out write can
    # still be found by reconciliation instead of resent.
    client_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_alpaca_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions.id"), nullable=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    leg: Mapped[str] = mapped_column(String(16), nullable=False)  # entry | stop | target | exit
    qty: Mapped[int] = mapped_column(nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Fill(Base):
    """A fill for an order."""

    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    qty: Mapped[int] = mapped_column(nullable=False)
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    filled_at: Mapped[datetime] = mapped_column(nullable=False)
    slippage: Mapped[Decimal | None] = mapped_column(Money, nullable=True)


class Position(Base):
    """An open or closed position."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # agent = opened by a stored decision; adopted = found at the broker and
    # acknowledged later. Adopted P&L is reported apart from agent trades.
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="agent")
    # Adopted positions get no model calls and no exits until acknowledged.
    pending_ack: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    entry_decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions.id"), nullable=True)
    qty: Mapped[int] = mapped_column(nullable=False)
    avg_entry_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    current_stop_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    # A same-day exit decision deferred by the day-trade rule: {at, reason}.
    # Shown on the position and included in the next session's first snapshot.
    deferred_exit: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    realized_pnl_raw: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    realized_pnl_adjusted: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Outcome(Base):
    """Post-decision price outcomes (docs/SPEC.md, Outcome tracking).

    Each price stores the timestamp of the 5-minute bar it came from, so
    clamped (early close) or late measurements stay visible.
    """

    __tablename__ = "outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decisions.id"), nullable=False)
    price_1h: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    price_1h_at: Mapped[datetime | None] = mapped_column(nullable=True)
    price_1session: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    price_1session_at: Mapped[datetime | None] = mapped_column(nullable=True)
    price_5sessions: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    price_5sessions_at: Mapped[datetime | None] = mapped_column(nullable=True)
    best_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    best_price_at: Mapped[datetime | None] = mapped_column(nullable=True)
    worst_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    worst_price_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # For trades: which of the stop or target levels price touched first
    # ("stop" | "target" | "neither"). None until a trade's bars were read.
    first_touch: Mapped[str | None] = mapped_column(String(16), nullable=True)


class EquitySnapshot(Base):
    """Equity curve and benchmark."""

    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    time: Mapped[datetime] = mapped_column(nullable=False, index=True)
    equity: Mapped[Decimal] = mapped_column(Money, nullable=False)
    cash: Mapped[Decimal] = mapped_column(Money, nullable=False)
    spy_benchmark_value: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    eur_usd_rate: Mapped[Decimal | None] = mapped_column(Money, nullable=True)


class DayTrade(Base):
    """Pattern-day-trade record."""

    __tablename__ = "day_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[datetime] = mapped_column(nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    order_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class Earning(Base):
    """Earnings calendar event."""

    __tablename__ = "earnings"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    date: Mapped[datetime] = mapped_column(nullable=False)
    time_of_day: Mapped[str] = mapped_column(String(16), nullable=False)  # normalised
    hour_raw: Mapped[str] = mapped_column(String(16), nullable=False)  # Finnhub raw value
    fetched_at: Mapped[datetime] = mapped_column(nullable=False)


class CorporateAction(Base):
    """Split or dividend."""

    __tablename__ = "corporate_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(16), nullable=False)  # split | dividend
    date: Mapped[datetime] = mapped_column(nullable=False)
    ratio_or_amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)


class UniversePoolRow(Base):
    """One symbol in the screened-mode pool (docs/SPEC.md, Larger universe).

    Seeded from ``backend/universe/pool.csv`` the first time screened mode
    starts, then edited on the Universe screen. Removed rows keep their row
    with ``removed_at`` set — a held symbol stays in the pool until its
    position is closed.
    """

    __tablename__ = "universe_pool"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    sector: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # default | added | import
    added_at: Mapped[datetime] = mapped_column(nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class UniverseScreenRow(Base):
    """One symbol's result in one daily liquidity screen.

    Written once per trading day per pool symbol; the eligible list for a
    session is fixed at screen time. ``failed_in_a_row`` counts consecutive
    screens the symbol failed — an eligible symbol only drops out at the
    configured threshold (default 3).
    """

    __tablename__ = "universe_screens"
    __table_args__ = (
        UniqueConstraint("session_date", "symbol", name="uq_universe_screens_day_symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_date: Mapped[date] = mapped_column(nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # eligible | excluded | warming_up
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Per-check results, e.g. {"history": "pass", ..., "failed_check": "one_share"}.
    check_results: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_close: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    avg_dollar_volume: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    # 20-session return minus SPY's, in percentage points.
    return_vs_spy: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    rank: Mapped[int | None] = mapped_column(nullable=True)
    failed_in_a_row: Mapped[int] = mapped_column(nullable=False, default=0)


class SettingsVersion(Base):
    """Versioned settings changes."""

    __tablename__ = "settings_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(nullable=False, unique=True)
    settings: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Secret(Base):
    """One API key, Fernet-encrypted (docs/SPEC.md, API keys).

    The plaintext is never stored, logged or returned by the API; reads
    expose only the last four characters and the test state. The Fernet
    key lives in ``SA_DATA_DIR/secret.key`` (mode 600), never in the DB.
    """

    __tablename__ = "secrets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    last4: Mapped[str] = mapped_column(String(8), nullable=False)
    saved_at: Mapped[datetime] = mapped_column(nullable=False)
    tested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    test_result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Short non-secret context for the last test (masked account number or
    # the failure reason); never the key itself.
    test_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Approval(Base):
    """A supervised-mode entry waiting for Marc's decision (docs/SPEC.md).

    Created when the execution mode is ``supervised`` and an entry passes
    the gates; expires at the next full cycle or is replaced by a newer
    pending approval for the same symbol.
    """

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decisions.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    qty: Mapped[int] = mapped_column(nullable=False)
    limit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    stop_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    target_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    # pending | approved | rejected | expired
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Run(Base):
    """One performance run between paper-account resets (docs/SPEC.md).

    Created by "Start new run" after a paper reset; restarts the SPY
    benchmark, the drawdown-kill peak, the losing streak and the daily
    loss baseline. PR 7 lets Performance pick earlier runs.
    """

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    starting_equity: Mapped[Decimal] = mapped_column(Money, nullable=False)
    spy_close: Mapped[Decimal | None] = mapped_column(Money, nullable=True)


class Review(Base):
    """A written weekly review or daily briefing (docs/SPEC.md, Reviews).

    Code computes the facts; the model only writes the text. ``source`` is
    "model" when the model's text passed the number check, else "template"
    with ``reason`` naming the fallback (for example "number check failed",
    "no OpenAI key", "model call failed", "cost cap reached").
    """

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("kind", "period", name="uq_reviews_kind_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # weekly | briefing
    period: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # "2026-W41" | "2026-10-19"
    facts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Main prose: the weekly summary or the briefing body.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured fields the GUI renders: {observations, suggestion} for weekly.
    content: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # model | template
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tokens: Mapped[int | None] = mapped_column(nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    due_at: Mapped[datetime] = mapped_column(
        nullable=False
    )  # scheduled instant; a caught-up review keeps its original date
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class Verdict(Base):
    """Draft or confirmed verdict on one decision (docs/SPEC.md, Reviews).

    Written once the decision's 5-session outcome is in; Marc confirms or
    changes it. ``source`` is "draft" until then, "marc" after. Only
    confirmed verdicts count in reviews.
    """

    __tablename__ = "verdicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(
        ForeignKey("decisions.id"), nullable=False, unique=True
    )
    verdict: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # good_call | bad_call | unclear
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # draft | marc
    # Who wrote the draft: the model or the plain template after a fallback.
    draft_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="model"
    )
    # Draft-call spend; the review monthly cap covers verdict calls too.
    cost: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    decision: Mapped[Decision] = relationship()


class Event(Base):
    """Reconciliation, kill switch, error and acknowledgment events."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    time: Mapped[datetime] = mapped_column(nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    # Blocking events (reconciliation mismatches) hold new entries until set.
    acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)
