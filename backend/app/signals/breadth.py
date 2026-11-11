"""Breadth and sectors, collected per cycle (docs/SPEC.md, Breadth and
sectors — item 068).

Like the VIX this is collection only: the four breadth shares over the
cycle's universe and the 11 sector ETFs' strength vs SPY are stored on
the cycle and on each candidate decision's ``market_context`` — never
part of the model's input and never a trading input.

Everything reads indicators the cycle already computed (the per-symbol
snapshots) and the cached bars — no new downloads happen here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.config import AppConfig
from app.data.models import Bar1h
from app.data.service import MarketDataService
from app.market.clock import NY_TZ
from app.signals.indicators import relative_strength_pct
from app.signals.service import SymbolEval

logger = logging.getLogger(__name__)

BREADTH_FIELD_NAMES = (
    "breadth_above_sma50_pct",
    "breadth_above_sma200_pct",
    "breadth_ema_up_pct",
    "breadth_up_today_pct",
)


def _pct(part: int, whole: int) -> Decimal | None:
    if whole <= 0:
        return None
    return (Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.01"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _prev_close(item: SymbolEval, today: Any) -> Decimal | None:
    """The close before today's session from the item's completed daily
    bars — ``daily`` only holds sessions already closed, so after today's
    close ``daily[-1]`` is today and the previous close is ``daily[-2]``."""
    daily = item.daily
    if not daily:
        return None
    if daily[-1].date == today:
        return daily[-2].close if len(daily) >= 2 else None
    return daily[-1].close


def breadth_fields(
    items: list[SymbolEval], *, min_symbols: int
) -> dict[str, Decimal | None]:
    """The four breadth shares over the cycle's universe.

    Each share counts only the symbols whose inputs exist; a share is
    null when fewer than ``min_symbols`` symbols were computed (docs/
    SPEC.md, Breadth and sectors).
    """
    counts = {name: [0, 0] for name in BREADTH_FIELD_NAMES}

    def tally(name: str, value: bool | None) -> None:
        if value is not None:
            counts[name][1] += 1
            counts[name][0] += int(value)

    for item in items:
        snap = item.snapshot
        tally(
            "breadth_above_sma50_pct",
            snap.daily_close > snap.daily_sma_fast
            if snap.daily_close is not None and snap.daily_sma_fast is not None
            else None,
        )
        tally(
            "breadth_above_sma200_pct",
            snap.daily_close > snap.daily_sma_slow
            if snap.daily_close is not None and snap.daily_sma_slow is not None
            else None,
        )
        tally(
            "breadth_ema_up_pct",
            snap.ema_fast > snap.ema_slow
            if snap.ema_fast is not None and snap.ema_slow is not None
            else None,
        )
        last_5m = item.today_5m[-1].close if item.today_5m else None
        prev = _prev_close(item, item.today_5m[0].start.astimezone(NY_TZ).date()
                           if item.today_5m else None)
        tally(
            "breadth_up_today_pct",
            last_5m > prev
            if last_5m is not None and prev is not None
            else None,
        )
    return {
        name: (
            _pct(above, total)
            if total >= min_symbols
            else None
        )
        for name, (above, total) in counts.items()
    }


@dataclass
class CycleMarket:
    """One cycle's breadth fields and sector table, plus what decision
    ``market_context`` rows need (the ETFs' 1h bars for rs_vs_sector)."""

    breadth: dict[str, Decimal | None]
    # sector -> {"etf", "rs_vs_spy_pct", "rank"} — JSON-ready floats.
    sectors: dict[str, dict[str, Any]]
    _etf_bars: dict[str, list[Bar1h]] = field(default_factory=dict, repr=False)
    _lookback: int = 20

    def columns(self) -> dict[str, Any]:
        """The ``cycles`` columns this cycle stores."""
        return {
            **self.breadth,
            "sectors": _jsonable(self.sectors),
        }

    def decision_context(
        self, sector: str | None, bars_1h: list[Bar1h]
    ) -> dict[str, Any]:
        """The four ``market_context`` fields for one candidate.

        Null without an ETF for the sector or without its bars (docs/
        SPEC.md, Breadth and sectors — Per candidate).
        """
        entry = self.sectors.get(sector) if sector is not None else None
        etf_bars = (
            self._etf_bars.get(str(entry["etf"])) if entry is not None else None
        )
        rs_sector = (
            self._rs_vs(bars_1h, etf_bars) if etf_bars else None
        )
        return {
            "sector_etf": entry["etf"] if entry is not None else None,
            "sector_rank": entry["rank"] if entry is not None else None,
            "sector_rs_vs_spy_pct": (
                entry["rs_vs_spy_pct"] if entry is not None else None
            ),
            "rs_vs_sector_pct": _jsonable(rs_sector),
        }

    def _rs_vs(
        self, symbol_bars: list[Bar1h], benchmark_bars: list[Bar1h]
    ) -> Decimal | None:
        """The stock against its sector ETF — the same matched-close
        ``relative_strength_pct`` the snapshots use for SPY."""
        bench_by_start = {b.start: b.close for b in benchmark_bars}
        matched = [
            (b.close, bench_by_start[b.start])
            for b in symbol_bars
            if b.start in bench_by_start
        ]
        return relative_strength_pct(
            [pair[0] for pair in matched],
            [pair[1] for pair in matched],
            self._lookback,
        )


class BreadthService:
    """Per-cycle breadth + sector computation; no fetching of its own."""

    def __init__(
        self,
        market_data: MarketDataService,
        config: AppConfig,
    ) -> None:
        self.market_data = market_data
        self.config = config

    def sector_etfs(self) -> dict[str, str]:
        """``market.sector_etfs``: sector name -> ETF symbol."""
        raw = self.config.get("market", "sector_etfs", default={}) or {}
        return {
            str(sector): str(etf).upper()
            for sector, etf in raw.items()
        }

    def reference_symbols(self) -> list[str]:
        """The sector ETFs — bars every cycle, never candidates."""
        return sorted(set(self.sector_etfs().values()))

    def _hourly(
        self, symbol: str, sessions: list[Any], now: datetime
    ) -> list[Bar1h]:
        """Closed, complete session-aligned 1h bars — the same filter the
        signals service applies."""
        return [
            b
            for b in self.market_data.get_1h_bars_range(symbol, sessions)
            if b.complete and b.end <= now
        ]

    def cycle_market(
        self, items: list[SymbolEval], now: datetime
    ) -> CycleMarket:
        """Breadth shares and the sector table for one cycle.

        Never raises and never waits on the network: cached bars only —
        missing ETF bars leave that sector's fields null and the rest
        ranked (docs/SPEC.md, Breadth and sectors).
        """
        try:
            return self._compute(items, now)
        except Exception:  # noqa: BLE001 - breadth never blocks a cycle
            logger.exception("breadth/sector computation failed")
            return CycleMarket(
                breadth=dict.fromkeys(BREADTH_FIELD_NAMES),
                sectors={},
            )

    def _compute(self, items: list[SymbolEval], now: datetime) -> CycleMarket:
        min_symbols = int(
            self.config.get("market", "breadth", "min_symbols", default=20)
        )
        breadth = breadth_fields(items, min_symbols=min_symbols)

        today = now.astimezone(NY_TZ).date()
        count = int(
            self.config.get("schedule", "history_sessions", default=60)
        )
        sessions = self.market_data.recent_sessions(today, count)
        lookback = int(
            self.config.get("indicators", "rs_lookback", default=20)
        )
        spy_1h = self._hourly("SPY", sessions, now)
        spy_by_start = {b.start: b.close for b in spy_1h}

        etf_map = self.sector_etfs()
        etf_bars: dict[str, list[Bar1h]] = {}
        rs_by_sector: dict[str, Decimal | None] = {}
        for sector, etf in etf_map.items():
            bars = self._hourly(etf, sessions, now)
            etf_bars[etf] = bars
            matched = [
                (b.close, spy_by_start[b.start])
                for b in bars
                if b.start in spy_by_start
            ]
            rs_by_sector[sector] = relative_strength_pct(
                [pair[0] for pair in matched],
                [pair[1] for pair in matched],
                lookback,
            )

        # Rank the ETFs with a value, strongest first; ties break on the
        # sector name so the table is deterministic.
        ranked = sorted(
            (s for s in etf_map if rs_by_sector[s] is not None),
            key=lambda s: (-(rs_by_sector[s] or Decimal(0)), s),
        )
        rank_by_sector = {s: i + 1 for i, s in enumerate(ranked)}
        sectors = {
            sector: {
                "etf": etf,
                "rs_vs_spy_pct": _jsonable(rs_by_sector[sector]),
                "rank": rank_by_sector.get(sector),
            }
            for sector, etf in etf_map.items()
        }
        return CycleMarket(
            breadth=breadth,
            sectors=sectors,
            _etf_bars=etf_bars,
            _lookback=lookback,
        )
