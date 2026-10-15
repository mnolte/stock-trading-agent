from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import get_config
from app.market.calendar import CalendarProvider, WeekdayCalendarProvider
from app.market.clock import SessionClock, SessionState, make_clock
from app.signals.service import SignalService

router = APIRouter()


def _calendar(request: Request) -> CalendarProvider:
    store = getattr(request.app.state, "calendar_store", None)
    return store if store is not None else WeekdayCalendarProvider()


def _calendar_loaded(request: Request) -> bool:
    store = getattr(request.app.state, "calendar_store", None)
    return getattr(store, "is_loaded", True)


def _eur_rate(request: Request) -> dict[str, object]:
    """Latest ECB rate for EUR display; never stale or zero."""
    market_data = getattr(request.app.state, "market_data", None)
    result = getattr(market_data, "latest_fx", None) if market_data else None
    rate = result.data if result is not None and result.error is None else None
    return {
        "available": rate is not None,
        "rate": str(rate.usd_per_eur) if rate is not None else None,
    }


def _session_state(request: Request) -> SessionState:
    clock = SessionClock(make_clock(), calendar=_calendar(request))
    return clock.state()


def _signals(request: Request) -> SignalService | None:
    return getattr(request.app.state, "signal_service", None)


def _regime_label(request: Request) -> str:
    """Live regime from SPY daily bars; 'unavailable' until data is in."""
    signals = _signals(request)
    if signals is None:
        return "unavailable"
    try:
        return signals.regime(signals.clock.now()).label
    except Exception:  # noqa: BLE001
        return "unavailable"


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": get_config().env.mode}


@router.get("/session")
def session_state(request: Request) -> dict[str, object]:
    state = _session_state(request)
    return {
        "state": state.state,
        "next_cycle": state.next_cycle.isoformat() if state.next_cycle else None,
        "next_open": state.next_open.isoformat() if state.next_open else None,
        "next_close": state.next_close.isoformat() if state.next_close else None,
        "display_time": state.display_time,
        "calendar_loaded": _calendar_loaded(request),
    }


@router.get("/header")
def header_state(request: Request) -> dict[str, object]:
    """Skeleton header payload: PAPER badge plus placeholders for equity/P&L/regime."""
    session = _session_state(request)
    return {
        "paper": True,
        "mode": get_config().env.mode,
        "session": {
            "state": session.state,
            "display_time": session.display_time,
            "next_cycle": session.next_cycle.isoformat() if session.next_cycle else None,
            "calendar_loaded": _calendar_loaded(request),
        },
        "equity_usd": "10,184.30",
        "equity_eur": "8,749.40",
        "today_usd": "+42.10",
        "today_eur": "+36.17",
        "regime": _regime_label(request),
        "kill_switch": False,
        "eur": _eur_rate(request),
    }


@router.get("/watchlist")
def watchlist(request: Request) -> dict[str, object]:
    """Per-symbol rules status for the dashboard watchlist."""
    signals = _signals(request)
    if signals is None:
        return {"computed_at": None, "regime": None, "rows": []}
    result = signals.watchlist(signals.clock.now())
    return {
        "computed_at": result.computed_at.isoformat(),
        "regime": {
            "trend": result.regime.trend,
            "volatility": result.regime.volatility,
            "label": result.regime.label,
            "blocks_entries": result.regime.blocks_entries,
        },
        "rows": [
            {
                "symbol": row.symbol,
                "price": str(row.price) if row.price is not None else None,
                "day_change_pct": (
                    str(row.day_change_pct)
                    if row.day_change_pct is not None
                    else None
                ),
                "rules": {
                    "status": row.rules.status,
                    "reason": row.rules.reason,
                    "checks": [
                        {
                            "name": check.name,
                            "passed": check.passed,
                            "detail": check.detail,
                        }
                        for check in row.rules.checks
                    ],
                },
                "earnings_date": (
                    row.earnings_date.isoformat() if row.earnings_date else None
                ),
                "last_action": row.last_action,
                "last_action_time": (
                    row.last_action_time.isoformat()
                    if row.last_action_time
                    else None
                ),
            }
            for row in result.rows
        ],
    }
