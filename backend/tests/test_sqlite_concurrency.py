"""SQLite concurrency: shadows write while live jobs write (PR 13 review).

The database runs in WAL mode with a busy timeout, so a writer waits for
the lock instead of failing; and the shadow runner never holds a write
transaction across an await, because live jobs share its event loop.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.config import get_config
from app.db.base import Base, get_engine, get_session_maker
from app.db.models import Cycle, Decision, Run, ShadowEquity, Strategy
from app.decide.client import FakeDecisionClient
from app.market.calendar import WeekdayCalendarProvider
from app.market.clock import NY_TZ, UTC, FakeClock
from app.shadows.runner import ShadowRunner
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.lock_probe import LockProbe

pytestmark = pytest.mark.anyio


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


@pytest.mark.parametrize("iteration", range(2))
def test_scratch_schema_copy_is_empty_isolated_and_uses_production_pragmas(
    session_factory: sessionmaker[Session], empty_db_template: Path, iteration: int,
) -> None:
    with session_factory() as session:
        assert session.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert session.execute(text("PRAGMA busy_timeout")).scalar_one() == 30000
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert session.execute(text("PRAGMA temp_store")).scalar_one() == 2
        assert session.execute(text("PRAGMA mmap_size")).scalar_one() == 268435456
        assert session.execute(text("PRAGMA cache_size")).scalar_one() == -64000
        for table in Base.metadata.sorted_tables:
            assert session.execute(select(func.count()).select_from(table)).scalar_one() == 0
        session.add(Run(started_at=datetime(2026, 10, 15), starting_equity=Decimal(iteration)))
        session.commit()
        assert session.execute(select(func.count(Run.id))).scalar_one() == 1
    with sqlite3.connect(empty_db_template) as template:
        assert template.execute("SELECT count(*) FROM runs").fetchone() == (0,)


def test_foreign_keys_are_enforced(session_factory: sessionmaker[Session]) -> None:
    """Declared FKs are advisory no longer: an orphan row is refused."""
    with session_factory() as session:
        session.add(
            Decision(
                cycle_id=999999,
                symbol="AAA",
                context="candidate",
                final_action="skip",
                prompt_version="v1",
                prompt_hash="x",
                model="test",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_engine_uses_wal_and_busy_timeout() -> None:
    with get_engine().connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        timeout_ms = conn.execute(text("PRAGMA busy_timeout")).scalar()
    expected = float(get_config().get("storage", "sqlite_busy_timeout_seconds")) * 1000
    assert timeout_ms == int(expected)


def test_live_write_waits_for_a_shadow_write_and_never_fails() -> None:
    """A shadow transaction holds the write lock; a live cycle write made
    meanwhile waits for it and then succeeds."""
    Base.metadata.create_all(get_engine())
    sf = get_session_maker()
    with sf() as session:
        strategy = Strategy(
            name="A", kind="rules_only", status="active", created_at=datetime(2026, 10, 1)
        )
        session.add(strategy)
        session.commit()
        sid = strategy.id

    shadow_has_lock = threading.Event()
    errors: list[BaseException] = []

    def shadow_write() -> None:
        try:
            with sf() as s:
                s.add(
                    ShadowEquity(
                        strategy_id=sid,
                        time=datetime(2026, 10, 15, 15, 0),
                        equity=Decimal("10000"),
                        cash=Decimal("10000"),
                    )
                )
                s.flush()  # the write lock is held from here
                shadow_has_lock.set()
                time.sleep(0.6)
                s.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=shadow_write)
    thread.start()
    assert shadow_has_lock.wait(5)
    started = time.monotonic()
    with sf() as session:
        session.add(
            Cycle(
                kind="cycle",
                scheduled_time=datetime(2026, 10, 15, 15, 46),
                started_at=datetime(2026, 10, 15, 15, 46),
                status="running",
            )
        )
        session.commit()  # must not raise "database is locked"
    waited = time.monotonic() - started
    thread.join(5)
    assert not thread.is_alive()
    assert errors == []
    assert waited > 0.3  # it really waited for the shadow's lock
    with sf() as session:
        assert session.execute(select(func.count(Cycle.id))).scalar_one() == 1
        assert session.execute(select(func.count(ShadowEquity.id))).scalar_one() == 1


async def test_shadow_runner_holds_no_write_lock_while_awaiting_a_model() -> None:
    from tests.test_shadows import _snap

    Base.metadata.create_all(get_engine())
    sf = get_session_maker()
    cycle_time = datetime(2026, 10, 15, 10, 46, tzinfo=NY_TZ)
    with sf() as session:
        session.add(Run(started_at=datetime(2026, 10, 1, 13, 0), starting_equity=Decimal("10000")))
        cycle = Cycle(
            kind="cycle",
            scheduled_time=_naive(cycle_time),
            started_at=_naive(cycle_time),
            status="done",
        )
        session.add(cycle)
        session.flush()
        from app.db.models import Decision

        for symbol in ("MSFT", "AAPL", "NVDA"):
            session.add(
                Decision(
                    cycle_id=cycle.id,
                    symbol=symbol,
                    context="candidate",
                    input_snapshot=_snap(symbol),
                    final_action="skip",
                    prompt_version="decision.v1",
                    prompt_hash="0" * 64,
                    model="fake-decision",
                )
            )
        session.add(
            Strategy(
                name="A",
                kind="llm",
                prompt_version="decision.v1",
                model="gpt-4o-mini",
                temperature=0.0,
                status="active",
                created_at=datetime(2026, 10, 1),
            )
        )
        session.commit()
        cycle_id = cycle.id
    # While each model call is awaited, the probe takes the write lock
    # from another connection with no wait at all — it must get it.
    probe = LockProbe(
        "model", FakeDecisionClient(enter_symbols={"MSFT"}), get_config().env.sa_db_path
    )
    runner = ShadowRunner(
        config=get_config(),
        clock=FakeClock(cycle_time + timedelta(minutes=1)),
        calendar=WeekdayCalendarProvider(),
        client_factory=lambda s: probe,
        session_factory=sf,
        bars=lambda symbol, start, end: [],
    )
    report = await runner.after_cycle(cycle_id)
    assert report.strategies[0].calls == 3
    # Every probe got the write lock immediately (no "database is locked"),
    # even after the first call stored a row and an entry.
    assert probe.calls == ["complete"] * 3
    assert probe.violations == []
