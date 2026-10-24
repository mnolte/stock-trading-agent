from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, String, Text, UniqueConstraint
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
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
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
    # Why no model answer counts: rules | call_cap | price_stale | price_suspect | model_error
    no_call_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(32), nullable=False)
    tokens: Mapped[int | None] = mapped_column(nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    cost_source: Mapped[str] = mapped_column(String(16), nullable=False, default="priced")
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True when the answer came from llm_cache (a backtest replay) — the
    # call cost nothing and the row keeps the stored token counts.
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


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
    active_until: Mapped[datetime | None] = mapped_column(nullable=True)
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
    initial_stop: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
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
    cost_source: Mapped[str] = mapped_column(String(16), nullable=False, default="priced")
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
    # Which fallback made a template draft ("model call failed", "number
    # check failed"…) — the reviews table records this in ``reason``; the
    # LLM inspector needs it to tell a sent call from a skipped one.
    draft_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Draft-call spend; the review monthly cap covers verdict calls too.
    cost: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    cost_source: Mapped[str] = mapped_column(String(16), nullable=False, default="priced")
    # The facts the draft call was written from, so the LLM inspector can
    # rebuild the user part of the prompt (PR 18).
    facts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    decision: Mapped[Decision] = relationship()


class LlmCall(Base):
    """One request actually sent to OpenAI (docs/SPEC.md, LLM call history).

    Written for live decisions, shadow decisions and reviews in the same
    transaction as the linked row, after the call returned. Calls that
    never happened — rules ``no_call``, the call cap, price checks, a
    missing key, a reached cost cap, missed shadow calls — get no row.
    ``outcome`` and ``reasons`` are set once at write time. ``backfilled``
    marks rows the migration reconstructed from older data; their
    latency, HTTP status and request id were never recorded.
    """

    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_sent_id", "sent_at", "id"),
        Index("ix_llm_calls_session_sent", "session_date", "sent_at"),
        Index("ix_llm_calls_outcome_sent", "outcome", "sent_at"),
        Index("ix_llm_calls_source_sent", "source", "sent_at"),
        Index("ix_llm_calls_symbol_sent", "symbol", "sent_at"),
        # Summary/index-only aggregates and per-dimension option counts on
        # the 100k-call fixture budget (300 ms): one covering scan for the
        # session/outcome/cost aggregates, one index per option dimension
        # the spec's four indexes don't already cover, and latency_ms for
        # the median/p95 offset lookups. A call is written once per model
        # request, so index overhead is not a factor.
        Index(
            "ix_llm_calls_aggregates",
            "session_date",
            "source",
            "outcome",
            "cost",
        ),
        Index("ix_llm_calls_action", "final_action"),
        Index("ix_llm_calls_model", "model"),
        Index("ix_llm_calls_prompt", "prompt_version"),
        Index("ix_llm_calls_strategy", "strategy_id"),
        Index("ix_llm_calls_latency", "latency_ms"),
        Index("ix_llm_calls_reasons", "reasons"),
        Index(
            "ix_llm_calls_dims",
            "outcome", "source", "symbol", "final_action",
            "model", "prompt_version", "strategy_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(nullable=False)
    session_date: Mapped[date] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # live | shadow | review
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # decision | briefing | weekly | verdict
    strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategies.id"), nullable=True
    )
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("decisions.id"), nullable=True
    )
    shadow_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("shadow_decisions.id"), nullable=True
    )
    review_id: Mapped[int | None] = mapped_column(
        ForeignKey("reviews.id"), nullable=True
    )
    verdict_id: Mapped[int | None] = mapped_column(
        ForeignKey("verdicts.id"), nullable=True
    )
    cycle_id: Mapped[int | None] = mapped_column(
        ForeignKey("cycles.id"), nullable=True
    )
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    context: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model: Mapped[str | None] = mapped_column(String(32), nullable=True)
    temperature: Mapped[float | None] = mapped_column(nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    cost_source: Mapped[str] = mapped_column(String(16), nullable=False, default="priced")
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    final_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # success | warning | error
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # The primary error reason (timeout | api_error | invalid_output |
    # refusal), the HTTP status and OpenAI's error code when it sent them.
    error_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    http_status: Mapped[int | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)  # scrubbed
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    backfilled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Notification(Base):
    """One notification event and its per-channel outcome (docs/SPEC.md,
    Notifications).

    Every alert the agent raises lands here once; ``channel_status`` records
    what each channel did — ``sent``, ``failed``, ``held back`` (repeat
    limit), ``quiet hours`` or ``queued`` while the background email send is
    still in flight.
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    time: Mapped[datetime] = mapped_column(nullable=False, index=True)
    # One of the configured event keys (kill_switch, missing_stop, …) or a
    # system event (test, quiet_summary).
    event: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # critical | warning | info
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # GUI page the alert links to, e.g. "/positions".
    link: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channels: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    channel_status: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Held by quiet hours until the end-of-window summary picks it up.
    held_for_quiet: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class SmtpSettings(Base):
    """The non-secret half of the email channel (docs/SPEC.md, Notifications).

    Saved on its own, outside the versioned settings, after a successful
    test send. The password is a ``secrets`` row (``smtp_password``) —
    encrypted, write-only, never here.
    """

    __tablename__ = "smtp_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    from_addr: Mapped[str] = mapped_column(String(255), nullable=False)
    to_addr: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


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
    # What the event touched: decision | order | fill | none. Only errors
    # with a real object count for Readiness (docs/SPEC.md, Readiness).
    affects: Mapped[str] = mapped_column(
        String(16), nullable=False, default="none"
    )
    # Error resolution: set when the error auto-resolves or Marc dismisses
    # it; ``resolution`` is auto | dismissed and ``note`` holds his words.
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReadinessSnapshot(Base):
    """One daily readiness evaluation, stored after the session's close.

    ``criteria`` is the full list of 15 criterion results, each
    ``{key, group, label, hint, value, target, progress, status, sample,
    estimate, estimate_date, note}`` (docs/SPEC.md, Readiness).
    """

    __tablename__ = "readiness_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "session_date", "run_id", name="uq_readiness_snapshots_day_run"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_date: Mapped[date] = mapped_column(nullable=False, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), nullable=False)
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ReadinessManual(Base):
    """One change to a fixed manual readiness item (append-only log).

    The latest row per ``item`` is its current state; every row is a
    History entry. Ticking or unticking requires a short note.
    """

    __tablename__ = "readiness_manual"

    id: Mapped[int] = mapped_column(primary_key=True)
    item: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(nullable=False)


class Strategy(Base):
    """A shadow strategy configuration (docs/SPEC.md, Strategies and shadows).

    Live is not a row: it is the strategy defined by the current settings
    version. ``overrides`` holds only the entry-rule and code-check keys a
    strategy may change (RSI range, minimum confidence, stop distance range,
    minimum reward to risk); risk gates, sizing, the day-trade rule and
    earnings handling are never overridable.
    """

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # llm | rules_only
    prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model: Mapped[str | None] = mapped_column(String(32), nullable=True)
    temperature: Mapped[float | None] = mapped_column(nullable=True)
    overrides: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # active | paused | archived
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Settings version saved through "Promote to live", if any.
    promoted_version: Mapped[int | None] = mapped_column(nullable=True)


class ShadowDecision(Base):
    """One shadow strategy's decision for one symbol in one live cycle.

    The market part of the input is the live decision's stored snapshot
    (``live_decision_id``); only the rebuilt position block is stored here.
    ``missed`` marks work skipped because the next live cycle or position
    check was due.
    """

    __tablename__ = "shadow_decisions"
    __table_args__ = (
        UniqueConstraint(
            "strategy_id", "cycle_id", "symbol", name="uq_shadow_decisions_scs"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id"), nullable=False, index=True
    )
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey("cycles.id"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    context: Mapped[str] = mapped_column(String(16), nullable=False)  # candidate | held
    live_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("decisions.id"), nullable=True
    )
    position: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    rules_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    model_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    gates_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    final_action: Mapped[str] = mapped_column(String(32), nullable=False)
    no_call_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tokens: Mapped[int | None] = mapped_column(nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    cost_source: Mapped[str] = mapped_column(String(16), nullable=False, default="priced")
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    missed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Why the work was missed: time (next live event due) | cost_cap.
    missed_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Only for a symbol live made no decision on (held by shadows only):
    # the snapshot live's cycle built for it.
    input_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ShadowPosition(Base):
    """A shadow's virtual entry order and the position it becomes.

    ``status``: pending (limit working) → open → closed, or pending →
    not_filled. ``entry`` and ``exit_price`` are the raw bar fill prices;
    ``pnl`` is net of slippage per side plus dividends, like live's adjusted
    P&L, and ``r`` is ``pnl`` over the risk at entry.
    """

    __tablename__ = "shadow_positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("shadow_decisions.id"), nullable=True
    )
    qty: Mapped[int] = mapped_column(nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    entry: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    initial_stop: Mapped[Decimal] = mapped_column(Money, nullable=False)
    stop: Mapped[Decimal] = mapped_column(Money, nullable=False)
    target: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pnl: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    r: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    deferred_exit: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # A pending exit (model, earnings or flatten) filled at the next bar's open.
    exit_pending: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exit_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Start of the last 5-min bar already checked for fills, stops, targets.
    bars_through: Mapped[datetime | None] = mapped_column(nullable=True)


class ShadowEquity(Base):
    """A shadow's virtual equity, written at every cycle and position check."""

    __tablename__ = "shadow_equity"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    time: Mapped[datetime] = mapped_column(nullable=False)
    equity: Mapped[Decimal] = mapped_column(Money, nullable=False)
    cash: Mapped[Decimal] = mapped_column(Money, nullable=False)


class LLMCache(Base):
    """A stored model answer, so a backtest rerun on the same inputs is free.

    Keyed by model, temperature, prompt version + hash and input snapshot
    hash; ``cycle_time`` is the decision's scheduled New York time, so the
    estimate counts rows inside the requested period (docs/SPEC.md,
    Backtesting, Model calls). Live and shadow calls write rows too — only
    the backtest reads them.
    """

    __tablename__ = "llm_cache"
    __table_args__ = (
        UniqueConstraint(
            "model",
            "temperature",
            "prompt_hash",
            "input_hash",
            name="uq_llm_cache_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    temperature: Mapped[float] = mapped_column(nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # SHA-256 of the exact user message (the serialised input snapshot).
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # The scheduled cycle time this answer was produced for (UTC, naive).
    cycle_time: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class Backtest(Base):
    """One backtest run's summary row in the live database.

    The run's real data lives in ``data/backtests/run-<id>/``: one
    live-schema database per strategy plus a read-only ``market.db``
    (docs/SPEC.md, Backtesting, Engine).
    """

    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    start_date: Mapped[date] = mapped_column(nullable=False)
    end_date: Mapped[date] = mapped_column(nullable=False)
    universe_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    # [{ref, name, kind, slug, model, prompt_version, temperature}]
    strategies: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    starting_equity: Mapped[Decimal] = mapped_column(Money, nullable=False)
    slippage_pct: Mapped[Decimal] = mapped_column(Money, nullable=False)
    # queued | downloading | running | done | cancelled
    #   | stopped_at_cost_cap | interrupted | refused
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    # "session 41 of 230" — committed after each session.
    progress: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cost_usd: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal(0))
    run_dir: Mapped[str] = mapped_column(String(255), nullable=False)
    process_id: Mapped[int | None] = mapped_column(nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set by the app while a live cycle or position check runs; the child
    # waits before every Alpaca download and every model call.
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    size_bytes: Mapped[int | None] = mapped_column(nullable=True)
    warnings: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    refuse_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
