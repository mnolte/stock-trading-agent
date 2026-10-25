"""Replay command: estimate/refusal, isolation from live tables, output
files (docs/SPEC.md, Replays)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from app.config import AppConfig
from app.data.cache import BarCache, _naive_utc
from app.db.models import Cycle, Decision, Run
from app.decide.client import FakeDecisionClient
from app.market.calendar import StaticCalendarProvider
from app.replay import replay, replay_spent_usd
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

DAY = date(2026, 10, 15)
SNAP = {
    "symbol": "MSFT",
    "context": "candidate",
    "price": 420.0,
    "stop_reference_price": "419.90",
    "last_bar_close": "419.90",
    "indicators_1h": {"atr14": 3.1},
    "levels": {},
    "headlines": [],
}


def _seed(session: Session) -> int:
    session.add(
        Run(started_at=_naive_utc(datetime(2026, 10, 14, 0, 0, tzinfo=UTC)),
            starting_equity=Decimal("100000"))
    )
    session.add(
        Cycle(
            kind="cycle",
            scheduled_time=_naive_utc(datetime(2026, 10, 15, 14, 0, tzinfo=UTC)),
            status="done",
        )
    )
    session.flush()
    cycle_id = session.execute(select(func.max(Cycle.id))).scalar_one()
    session.add(
        Decision(
            cycle_id=cycle_id,
            symbol="MSFT",
            context="candidate",
            input_snapshot=dict(SNAP),
            final_action="skip",
            prompt_version="decision.v1",
            prompt_hash="0" * 64,
            model="fake",
        )
    )
    session.commit()
    return cycle_id


async def test_replay_refuses_above_confirm_threshold(
    session_factory: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        _seed(session)
    config = AppConfig()
    # Threshold below any estimate → refused without --yes, and nothing
    # is written to the live tables or the replay dir.
    config._active["decision"]["replay"]["confirm_above_usd"] = 0.000001
    before = _counts(session_factory)
    out = await replay(
        prompt_version="decision.v2",
        from_day=DAY,
        to_day=DAY,
        model=None,
        assume_yes=False,
        session_factory=session_factory,
        client=FakeDecisionClient(),
        config=config,
        out_dir=tmp_path / "r1",
    )
    assert out is None
    assert _counts(session_factory) == before
    assert not (tmp_path / "r1" / "results.jsonl").exists()


async def test_replay_writes_files_and_no_live_tables(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    with session_factory() as session:
        _seed(session)
    config = AppConfig()
    config._active["decision"]["replay"]["confirm_above_usd"] = 999
    before = _counts(session_factory)
    out = await replay(
        prompt_version="decision.v2",
        from_day=DAY,
        to_day=DAY,
        model=None,
        assume_yes=True,
        session_factory=session_factory,
        client=FakeDecisionClient(),
        config=config,
        out_dir=tmp_path / "r2",
        sessions_provider=_provider(config, session_factory),
    )
    assert out is not None
    assert (out / "results.jsonl").exists()
    assert (out / "report.md").exists()
    assert (out / "meta.json").exists()
    report = (out / "report.md").read_text()
    assert "enter rate" in report and "rules-only" in report
    # Live tables untouched.
    assert _counts(session_factory) == before
    # Cost is recorded for the backtest monthly cap.
    meta = json.loads((out / "meta.json").read_text())
    assert "cost_usd" in meta


def _counts(sf: sessionmaker[Session]) -> dict[str, int]:
    with sf() as session:
        return {
            "decisions": session.execute(select(func.count(Decision.id))).scalar_one(),
            "cycles": session.execute(select(func.count(Cycle.id))).scalar_one(),
        }


def _provider(config: AppConfig, sf: sessionmaker[Session]) -> StaticCalendarProvider:
    from app.replay import _sessions_from_bars

    days = _sessions_from_bars(
        BarCache(sf),
        "MSFT",
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 10, 16, tzinfo=UTC),
    )
    return StaticCalendarProvider(days)


def test_replay_spent_usd_reads_meta(tmp_path: Path) -> None:
    rdir = tmp_path / "replays" / "r1"
    rdir.mkdir(parents=True)
    (rdir / "meta.json").write_text(
        json.dumps({"cost_usd": 0.42, "created_at": datetime.now(UTC).isoformat()})
    )
    total = replay_spent_usd(tmp_path, datetime.now(UTC) - __import__("datetime").timedelta(days=1))
    assert total == Decimal("0.42")
