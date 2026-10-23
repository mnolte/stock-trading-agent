"""The backtest broker: the simulated broker, filled by replayed 5-min bars.

Simulated fills (docs/SPEC.md, Backtesting, Data):

- a bracket entry fills when a *later* 5-min bar trades at or through the
  limit — at the limit, or at a better open when the bar gaps through it;
- stops and targets are checked on every bar once the entry filled; the
  stop counts on the fill bar itself, the target from the next bar, and a
  bar that touches both resolves to the stop;
- a gap through the stop fills at the bar's open; a gap up through the
  target fills at the open;
- a market sell (model, earnings or flatten exit) fills at the next
  bar's open;
- exits apply the run's slippage per side; the entry's limit price is the
  execution price (the market can only improve it);
- commission is zero.

Bars are read from the shared bar store only — never the live cache, never
the account. No clock-hour bars, no lookahead: only bars fully closed at
the injected "now" are replayed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.broker.fake import FakeBroker, _OrderRec
from app.data.models import Bar5m
from app.market.clock import Clock

BAR = timedelta(minutes=5)

# Statuses whose order can still work or fill.
_WORKING = {"new", "accepted", "partially_filled"}


class BacktestBroker(FakeBroker):
    """FakeBroker whose fills come from replayed historical bars."""

    def __init__(
        self,
        clock: Clock,
        bars: Callable[[str, datetime, datetime], list[Bar5m]],
        *,
        cash: Decimal,
        slippage_pct: Decimal,
    ) -> None:
        super().__init__(clock, cash=cash)
        self._bars = bars
        self._slip = slippage_pct / Decimal(100)
        # Per-symbol replay cursor: the last bar start already applied.
        self._replayed: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # replay

    async def tick(self, symbol: str, price: Decimal) -> None:
        """Marks only — fills are bar-driven via ``replay``, never ticks."""
        self.prices[symbol] = price

    async def submit_market_sell(
        self,
        *,
        symbol: str,
        qty: int,
        market_on_open: bool,
        client_order_id: str,
    ) -> Any:
        """Queue the sell; ``_apply_bar`` fills it at the next bar's open
        minus slippage, whatever the caller asked for."""
        return await super().submit_market_sell(
            symbol=symbol,
            qty=qty,
            market_on_open=True,
            client_order_id=client_order_id,
        )

    async def replay(self, now: datetime) -> None:
        """Apply every fully-closed 5-min bar since the last replay."""
        symbols = {
            rec.symbol
            for rec in self.orders.values()
            if rec.status in _WORKING or rec.status == "held"
        } | set(self.positions)
        for symbol in sorted(symbols):
            since = self._replayed.get(symbol)
            bars = [
                b
                for b in self._bars(
                    symbol, since or datetime.min.replace(tzinfo=now.tzinfo), now
                )
                if b.start + BAR <= now and (since is None or b.start >= since)
            ]
            for bar in bars:
                self.prices[symbol] = bar.close
                await self._apply_bar(symbol, bar)
            if bars:
                # +BAR so the same bar is never replayed twice.
                self._replayed[symbol] = bars[-1].start + BAR

    async def _apply_bar(self, symbol: str, bar: Bar5m) -> None:
        """Fill orders whose price the bar traded through, in order of submission."""
        recs = sorted(
            (
                rec
                for rec in self.orders.values()
                if rec.symbol == symbol and rec.status in _WORKING
            ),
            # Stop legs before target legs on the same bar: a bar that
            # touches both resolves to the stop (docs/SPEC.md).
            key=lambda r: (str(r.created_at), 0 if r.order_type == "stop" else 1),
        )
        filled_parents: set[str] = set()
        for rec in recs:
            # A leg filled earlier in this same bar cancels its siblings —
            # re-check instead of trusting the snapshot from loop start.
            if rec.status not in _WORKING:
                continue
            created = rec.created_at
            created_at = created if isinstance(created, datetime) else None
            if created_at is not None and bar.start < created_at:
                continue  # a later bar only, like the shadow replay
            if rec.side == "buy" and rec.order_type == "limit":
                if rec.limit_price is not None and bar.low <= rec.limit_price:
                    price = min(bar.open, rec.limit_price)
                    self._fill(rec, price)
                    filled_parents.add(rec.id)
                    await self._emit(rec, "fill")
                    # The stop counts on the fill bar; the target from the
                    # next one.
                    await self._check_legs(rec, bar, fill_bar=True)
            elif rec.side == "sell" and rec.order_type == "market":
                price = bar.open * (1 - self._slip)
                self._fill(rec, price)
                await self._emit(rec, "fill")
            elif rec.side == "sell":
                # A leg that went live on this bar counts as on its fill bar:
                # stop yes, target no.
                await self._check_exit(
                    rec,
                    bar,
                    fill_bar=(
                        rec.parent_id is not None
                        and rec.parent_id in filled_parents
                    ),
                )

    async def _check_legs(
        self, rec: _OrderRec, bar: Bar5m, *, fill_bar: bool
    ) -> None:
        """Check a just-filled bracket's legs on the fill bar."""
        for leg_id in rec.legs:
            leg = self.orders.get(leg_id)
            if leg is None or leg.status not in _WORKING:
                continue
            if leg.order_type == "stop" or not fill_bar:
                await self._check_exit(leg, bar, fill_bar=fill_bar)

    async def _check_exit(
        self, rec: _OrderRec, bar: Bar5m, *, fill_bar: bool
    ) -> None:
        """Stop first when a bar touches both legs (docs/SPEC.md)."""
        if rec.order_type == "stop" and rec.stop_price is not None:
            if bar.low <= rec.stop_price:
                price = min(bar.open, rec.stop_price) * (1 - self._slip)
                self._fill(rec, price)
                await self._emit(rec, "fill")
        elif rec.order_type == "limit" and rec.limit_price is not None:
            if fill_bar:
                return  # target counts from the next bar
            if bar.high >= rec.limit_price:
                price = max(bar.open, rec.limit_price) * (1 - self._slip)
                self._fill(rec, price)
                await self._emit(rec, "fill")
