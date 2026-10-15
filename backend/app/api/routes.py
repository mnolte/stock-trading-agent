from __future__ import annotations

from fastapi import APIRouter

from app.config import get_config
from app.market.clock import SessionClock, make_clock

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": get_config().env.mode}


@router.get("/session")
def session_state() -> dict[str, object]:
    clock = SessionClock(make_clock())
    state = clock.state()
    return {
        "state": state.state,
        "next_cycle": state.next_cycle.isoformat() if state.next_cycle else None,
        "next_open": state.next_open.isoformat() if state.next_open else None,
        "next_close": state.next_close.isoformat() if state.next_close else None,
        "display_time": state.display_time,
    }


@router.get("/header")
def header_state() -> dict[str, object]:
    """Skeleton header payload: PAPER badge plus placeholders for equity/P&L/regime."""
    clock = SessionClock(make_clock())
    session = clock.state()
    return {
        "paper": True,
        "mode": get_config().env.mode,
        "session": {
            "state": session.state,
            "display_time": session.display_time,
            "next_cycle": session.next_cycle.isoformat() if session.next_cycle else None,
        },
        "equity_usd": "10,184.30",
        "equity_eur": "8,749.40",
        "today_usd": "+42.10",
        "today_eur": "+36.17",
        "regime": "Up · normal vol",
        "kill_switch": False,
    }
