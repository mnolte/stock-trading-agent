"""Strategies API, Promote to live and startup price checks (PR 13)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from app.api.main import check_model_prices_at_startup, run_migrations
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import Cycle, Run, ShadowDecision, ShadowPosition, Strategy
from fastapi.testclient import TestClient
from sqlalchemy import select


def _new(client: TestClient, **body: object) -> int:
    payload = {"name": "B · Bigger model", "kind": "llm", "model": "gpt-4.1-mini", **body}
    response = client.post("/api/strategies", json=payload)
    assert response.status_code == 200, response.text
    return int(response.json()["id"])


def _closed_trades(strategy_id: int, n: int) -> None:
    with get_session_maker()() as session:
        run = session.execute(select(Run).order_by(Run.id.desc())).scalars().first()
        assert run is not None
        t = run.started_at + timedelta(days=1)
        for i in range(n):
            session.add(
                ShadowPosition(
                    strategy_id=strategy_id,
                    run_id=run.id,
                    symbol="MSFT",
                    status="closed",
                    qty=1,
                    entry=Decimal("100"),
                    initial_stop=Decimal("98"),
                    stop=Decimal("98"),
                    created_at=t,
                    expires_at=t,
                    opened_at=t,
                    closed_at=t + timedelta(days=1, minutes=i),
                    pnl=Decimal("2"),
                    r=Decimal("1"),
                )
            )
        session.commit()


def test_synthetic_quote_entry_count_is_for_filled_positions_in_current_run(
    client: TestClient,
) -> None:
    sid = _new(client)
    assert client.get("/api/strategies").json()["synthetic_quote_entries"] == 0
    _closed_trades(sid, 1)
    with get_session_maker()() as session:
        position = session.execute(select(ShadowPosition).where(
            ShadowPosition.strategy_id == sid,
        )).scalar_one()
        cycle = Cycle(kind="cycle", status="done", scheduled_time=position.created_at)
        session.add(cycle)
        session.flush()
        decision = ShadowDecision(
            strategy_id=sid, cycle_id=cycle.id, symbol="MSFT", context="candidate",
            final_action="enter", gates_result={"quote": {"synthetic": True}},
            created_at=position.created_at,
        )
        session.add(decision)
        session.flush()
        position.entry_decision_id = decision.id
        session.commit()
    assert client.get("/api/strategies").json()["synthetic_quote_entries"] == 1


def test_new_shadow_validation(client: TestClient) -> None:
    bad = client.post("/api/strategies", json={"name": "X", "kind": "llm", "model": "gpt-9"})
    assert bad.status_code == 422
    assert any("no price" in e for e in bad.json()["detail"])
    gate = client.post(
        "/api/strategies",
        json={"name": "X", "kind": "rules_only", "overrides": {"risk_per_trade_pct": 2}},
    )
    assert gate.status_code == 422
    assert any("not overridable" in e for e in gate.json()["detail"])
    for i in range(3):
        _new(client, name=f"S{i}")
    full = client.post("/api/strategies", json={"name": "S4", "kind": "rules_only"})
    assert full.status_code == 422
    assert any("slots" in e for e in full.json()["detail"])


def test_strategies_payload_and_promote_disabled_under_20(client: TestClient) -> None:
    sid = _new(client)
    data = client.get("/api/strategies").json()
    assert data["rows"][0]["role"] == "live"
    shadow = next(r for r in data["rows"] if r["id"] == sid)
    assert shadow["short"] == "B"
    assert shadow["promote"]["allowed"] is False
    assert shadow["promote"]["reason"] == "Needs at least 20 closed trades (has 0)"
    assert shadow["differences"]["differs"][0]["label"] == "Model"
    assert data["slots"] == {"used": 1, "max": 3}
    assert data["cost"]["cap_usd"] == "5"
    assert data["options"]["models"] == ["gpt-4.1-mini", "gpt-4o-mini"]

    promotion = client.get(f"/api/strategies/{sid}/promotion").json()
    assert promotion["allowed"] is False
    refused = client.post(
        "/api/settings",
        json={
            "settings": promotion["changes"],
            "reason": "promote",
            "promote_strategy_id": sid,
        },
    )
    assert refused.status_code == 409

    _closed_trades(sid, 19)
    assert client.get(f"/api/strategies/{sid}/promotion").json()["allowed"] is False


def test_promote_opens_settings_change_and_archives(client: TestClient) -> None:
    sid = _new(client)
    _closed_trades(sid, 20)
    promotion = client.get(f"/api/strategies/{sid}/promotion").json()
    assert promotion["allowed"] is True
    assert promotion["changes"] == {"decision": {"model": "gpt-4.1-mini"}}
    # Nothing is promoted until Marc saves it with a reason.
    with get_session_maker()() as session:
        assert session.get(Strategy, sid).status == "active"  # type: ignore[union-attr]
    no_reason = client.post(
        "/api/settings",
        json={"settings": promotion["changes"], "reason": "", "promote_strategy_id": sid},
    )
    assert no_reason.status_code == 422
    saved = client.post(
        "/api/settings",
        json={
            "settings": promotion["changes"],
            "reason": "B beat live over 20 trades",
            "promote_strategy_id": sid,
        },
    )
    assert saved.status_code == 200, saved.text
    with get_session_maker()() as session:
        strategy = session.get(Strategy, sid)
        assert strategy is not None
        assert strategy.status == "archived"
        assert strategy.promoted_version == saved.json()["version"]
    assert get_config().pending_version == saved.json()["version"]


def test_rules_only_can_not_be_promoted(client: TestClient) -> None:
    sid = _new(client, name="Rules only", kind="rules_only")
    _closed_trades(sid, 25)
    assert client.get(f"/api/strategies/{sid}/promotion").json()["allowed"] is False


def test_pause_resume_archive(client: TestClient) -> None:
    sid = _new(client)
    assert client.post(f"/api/strategies/{sid}/pause").json()["status"] == "paused"
    assert client.post(f"/api/strategies/{sid}/resume").json()["status"] == "active"
    assert client.post(f"/api/strategies/{sid}/archive").json()["status"] == "archived"
    assert client.post(f"/api/strategies/{sid}/resume").status_code == 409
    rows = client.get("/api/strategies").json()["rows"]
    assert all(r["id"] != sid for r in rows)


def test_live_adjusted_return_equals_performance(client: TestClient) -> None:
    perf = client.get("/api/performance").json()
    live = client.get("/api/strategies").json()["rows"][0]
    start = Decimal(perf["run"]["starting_equity"])
    expected = (Decimal(perf["summary"]["adjusted_usd"]) / start * 100).quantize(Decimal("0.01"))
    assert Decimal(live["return_pct"]) == expected
    assert live["raw_return_pct"] == perf["summary"]["return_pct"]


def test_startup_refused_with_unpriced_active_shadow_model() -> None:
    run_migrations()
    config = get_config()
    check_model_prices_at_startup(config)
    with get_session_maker()() as session:
        session.add(
            Strategy(
                name="Unpriced",
                kind="llm",
                prompt_version="decision.v1",
                model="gpt-9",
                temperature=0.0,
                status="active",
                created_at=datetime(2026, 10, 15, 12, 0),
            )
        )
        session.commit()
    with pytest.raises(RuntimeError, match="gpt-9"):
        check_model_prices_at_startup(config)


def test_weekly_facts_include_the_comparison(client: TestClient) -> None:
    from app.market.calendar import WeekdayCalendarProvider
    from app.market.clock import make_clock
    from app.reviews.facts import weekly_facts

    _new(client)
    with get_session_maker()() as session:
        facts = weekly_facts(
            session,
            config=get_config(),
            clock=make_clock(get_config()),
            calendar=WeekdayCalendarProvider(),
            monday=date(2026, 10, 12),
        )
    names = [row["name"] for row in facts["strategies"]]
    assert names == ["Live", "B · Bigger model"]
    assert "same_as_live_pct" in facts["strategies"][1]
