"""Replay command: estimate/refusal, isolation from live tables, output
files, replay statuses and --approx-reference (docs/SPEC.md, Replays)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from app.config import AppConfig
from app.data.cache import BarCache, _naive_utc
from app.db.models import Bar5m as Bar5mRow
from app.db.models import Cycle, Decision, Run
from app.decide.client import FakeDecisionClient
from app.market.calendar import StaticCalendarProvider
from app.replay import replay, replay_spent_usd
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

DAY = date(2026, 10, 15)
CYCLE_AT = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
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
# A pre-PR 15 snapshot: no stop_reference_price at all.
SNAP_NO_REF = {k: v for k, v in SNAP.items() if k != "stop_reference_price"}


def _seed_bars(session: Session, symbol: str = "MSFT") -> None:
    """Eight 5m bars around the snapshot price — enough for session
    levels, a last close for --approx-reference and a bar to score on."""
    base = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)  # 09:30 New York
    for i in range(8):
        o = Decimal("418") + Decimal(i) * Decimal("0.25")
        c = o + Decimal("0.10")
        session.add(
            Bar5mRow(
                symbol=symbol,
                start=base + timedelta(minutes=5 * i),
                open=o,
                high=c + Decimal("0.3"),
                low=o - Decimal("0.3"),
                close=c,
                volume=1000,
                feed="test",
            )
        )


def _seed(
    session: Session,
    snapshot: dict[str, Any] | None = None,
    action: str = "skip",
    *,
    bars: bool = False,
    cycle_at: datetime = CYCLE_AT,
) -> int:
    session.add(
        Run(
            started_at=_naive_utc(datetime(2026, 10, 14, 0, 0, tzinfo=UTC)),
            starting_equity=Decimal("100000"),
        )
    )
    session.add(
        Cycle(
            kind="cycle",
            scheduled_time=_naive_utc(cycle_at),
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
            input_snapshot=dict(snapshot if snapshot is not None else SNAP),
            final_action=action,
            prompt_version="decision.v1",
            prompt_hash="0" * 64,
            model="fake",
        )
    )
    if bars:
        _seed_bars(session)
    session.commit()
    return cycle_id


def _results(out: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]


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
        _seed(session, bars=True)
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


# ----------------------------------------------------------------------
# statuses: replayed / not_replayable / replayed_approx


def _replay_config() -> AppConfig:
    config = AppConfig()
    config._active["decision"]["replay"]["confirm_above_usd"] = 999
    return config


async def _run(
    sf: sessionmaker[Session],
    tmp_path: Path,
    name: str,
    *,
    approx: bool = False,
    config: AppConfig | None = None,
) -> Path:
    config = config or _replay_config()
    out = await replay(
        prompt_version="decision.v2",
        from_day=DAY,
        to_day=DAY,
        model=None,
        assume_yes=True,
        approx_reference=approx,
        session_factory=sf,
        client=FakeDecisionClient(),
        config=config,
        out_dir=tmp_path / name,
        sessions_provider=_provider(config, sf),
    )
    assert out is not None
    return out


async def test_missing_reference_is_not_replayable_without_flag(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    """A pre-PR 15 snapshot (no stop_reference_price) is never called
    no_options — it is not_replayable/missing_reference."""
    with session_factory() as session:
        _seed(session, snapshot=SNAP_NO_REF, bars=True)
    out = await _run(session_factory, tmp_path, "no-flag")
    (row,) = _results(out)
    assert row["status"] == "not_replayable"
    assert row["error"] == "missing_reference"
    assert row["model_action"] is None and row["cost_usd"] is None
    assert "missing_reference" in (out / "report.md").read_text()


async def test_missing_reference_replayed_approx_with_flag(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    """--approx-reference fills the reference with min(price, last closed
    5m close) and marks the row replayed_approx, reported separately."""
    with session_factory() as session:
        _seed(session, snapshot=SNAP_NO_REF, bars=True)
    out = await _run(session_factory, tmp_path, "approx", approx=True)
    (row,) = _results(out)
    assert row["status"] == "replayed_approx"
    assert row["model_action"] is not None  # the model was called
    assert row["offered"]  # the builder offered options
    report = (out / "report.md").read_text()
    assert "replayed_approx" in report
    assert "approximate reference" in report
    meta = json.loads((out / "meta.json").read_text())
    assert meta["statuses"]["replayed_approx"] == 1


@pytest.mark.parametrize("approx", [False, True])
async def test_price_unavailable_is_never_reconstructed(
    session_factory: sessionmaker[Session], tmp_path: Path, approx: bool
) -> None:
    """price "unavailable" stays not_replayable/price_unavailable, flag
    or no flag — the price is never reconstructed."""
    snapshot = {**SNAP_NO_REF, "price": "unavailable"}
    with session_factory() as session:
        _seed(session, snapshot=snapshot, bars=True)
    out = await _run(session_factory, tmp_path, f"unavailable-{approx}", approx=approx)
    (row,) = _results(out)
    assert row["status"] == "not_replayable"
    assert row["error"] == "price_unavailable"
    assert row["model_action"] is None and row["cost_usd"] is None


async def test_builder_rejection_is_no_options_with_reason(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    """When the builder itself returns nothing, the row is replayed and
    the error carries EntryOptions.reason."""
    config = _replay_config()
    config._active["decision"]["min_reward_risk"] = 999  # no target can qualify
    with session_factory() as session:
        _seed(session, bars=True)
    out = await _run(session_factory, tmp_path, "reject", config=config)
    (row,) = _results(out)
    assert row["status"] == "replayed"
    assert row["error"].startswith("no_options:")
    assert "999" in row["error"]
    assert row["model_action"] is None
    report = (out / "report.md").read_text()
    assert "no_options" in report


async def test_report_rates_exclude_non_replayed_rows(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    """Rates are computed over replayed rows only and state their base."""
    with session_factory() as session:
        _seed(session, snapshot=SNAP, action="skip", bars=True)
        # A second, pre-PR 15 snapshot in a later cycle — not replayed
        # without the flag, so the enter rate stays based on one row.
        session.add(
            Cycle(
                kind="cycle",
                scheduled_time=_naive_utc(CYCLE_AT + timedelta(minutes=10)),
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
                input_snapshot=dict(SNAP_NO_REF),
                final_action="skip",
                prompt_version="decision.v1",
                prompt_hash="0" * 64,
                model="fake",
            )
        )
        session.commit()
    out = await _run(session_factory, tmp_path, "mixed")
    rows = _results(out)
    assert {r["status"] for r in rows} == {"replayed", "not_replayable"}
    report = (out / "report.md").read_text()
    assert "replayed: 1" in report
    assert "not_replayable: 1" in report
    assert "1 of 1 replayed candidates" in report


async def test_live_entries_section_lists_v1_entries(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    """Every snapshot where live v1 entered is listed with what v2
    offered and chose, or the not_replayable reason."""
    with session_factory() as session:
        _seed(session, snapshot=SNAP, action="enter", bars=True)
        session.add(
            Cycle(
                kind="cycle",
                scheduled_time=_naive_utc(CYCLE_AT + timedelta(minutes=10)),
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
                input_snapshot=dict(SNAP_NO_REF),
                final_action="enter",
                prompt_version="decision.v1",
                prompt_hash="0" * 64,
                model="fake",
            )
        )
        session.commit()
    out = await _run(session_factory, tmp_path, "entries")
    report = (out / "report.md").read_text()
    assert "## Live entries" in report
    assert "offered" in report and "chose" in report
    assert "not_replayable: missing_reference" in report
