from types import SimpleNamespace
from typing import Any, cast

from app.api.routes import _eur_rate
from app.data.models import DataResult, FXRate
from fastapi import Request
from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["mode"] == "simulated"


def test_session_reports_calendar_loaded(client: TestClient) -> None:
    response = client.get("/api/session")
    assert response.status_code == 200
    data = response.json()
    assert data["calendar_loaded"] is True
    assert data["state"] in {"open", "closed", "last_cycle_done", "early_close"}
    assert "next_check" in data
    assert data["cycles_today"] >= 0


def test_header_is_paper(client: TestClient) -> None:
    response = client.get("/api/header")
    assert response.status_code == 200
    data = response.json()
    assert data["paper"] is True
    assert data["mode"] == "simulated"
    assert data["session"]["state"] in {"open", "closed", "last_cycle_done", "early_close"}
    assert "next_check" in data["session"]
    assert data["session"]["cycles_today"] >= 0
    assert data["eur"]["available"] is True


def test_watchlist_returns_rules_status_per_symbol(client: TestClient) -> None:
    response = client.get("/api/watchlist")
    assert response.status_code == 200
    data = response.json()
    assert data["computed_at"] is not None
    assert data["regime"]["trend"] in {"up", "down", "sideways"}
    rows = {row["symbol"]: row for row in data["rows"]}
    assert set(rows) == {
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM"
    }
    for row in rows.values():
        assert row["rules"]["status"] in {"pass", "fail", "held"}
        assert row["rules"]["checks"]
        if row["rules"]["status"] == "fail":
            assert row["rules"]["reason"]


def test_watchlist_serves_stored_snapshot(client: TestClient) -> None:
    # The endpoint must never recompute per request: the stored snapshot is
    # computed at startup/cycle time, so breaking recompute must not matter.
    service = cast(Any, client.app).state.signal_service

    def boom(now: object) -> None:
        raise AssertionError("watchlist recomputed per request")

    service.watchlist = boom
    response = client.get("/api/watchlist")
    assert response.status_code == 200
    assert len(response.json()["rows"]) == 10


def _request_with_fx(latest_fx: DataResult[FXRate | None] | None) -> SimpleNamespace:
    market_data = SimpleNamespace(latest_fx=latest_fx)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(market_data=market_data))
    )


def test_eur_available_when_rate_fetched() -> None:
    from datetime import date, datetime
    from decimal import Decimal

    from app.market.clock import UTC

    fx = FXRate(
        base="EUR",
        quote="USD",
        usd_per_eur=Decimal("1.1200"),
        for_date=date(2026, 10, 15),
        fetched_at=datetime(2026, 10, 15, tzinfo=UTC),
    )
    request = cast(Request, _request_with_fx(DataResult(data=fx, source="fake")))
    result = _eur_rate(request)
    assert result == {"available": True, "rate": "1.1200"}


def test_eur_unavailable_when_fx_failed() -> None:
    # A failed or missing fetch reports unavailable; never a stale or zero rate.
    failed = _eur_rate(
        cast(
            Request,
            _request_with_fx(DataResult(data=None, error="ecb down", source="ecb")),
        )
    )
    assert failed == {"available": False, "rate": None}
    missing = _eur_rate(cast(Request, _request_with_fx(None)))
    assert missing == {"available": False, "rate": None}
