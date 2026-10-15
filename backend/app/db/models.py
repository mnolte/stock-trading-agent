from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

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
    entry_decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions.id"), nullable=True)
    qty: Mapped[int] = mapped_column(nullable=False)
    avg_entry_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    current_stop_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    realized_pnl_raw: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    realized_pnl_adjusted: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Outcome(Base):
    """Post-decision price outcomes."""

    __tablename__ = "outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decisions.id"), nullable=False)
    price_1h: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    price_1session: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    price_5sessions: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    best_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    worst_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)


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


class SettingsVersion(Base):
    """Versioned settings changes."""

    __tablename__ = "settings_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(nullable=False, unique=True)
    settings: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Event(Base):
    """Reconciliation, kill switch, error and acknowledgment events."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    time: Mapped[datetime] = mapped_column(nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
