from pathlib import Path

import app.config as config_module
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
FIRST_REVISION = "fb346e483880"


def _upgrade(db: Path, revision: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SA_DB_PATH", str(db))
    monkeypatch.setattr(config_module, "_config", None)
    command.upgrade(Config(str(ALEMBIC_INI)), revision)


def test_integrity_migration_backfills_initial_stop_and_preserves_legacy_costs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "integrity.db"
    _upgrade(db, "f1a2b3c4d5e6", monkeypatch)
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO cycles (id, kind, scheduled_time, status) "
            "VALUES (1, 'cycle', '2026-10-22 15:46:00', 'done')"
        ))
        for decision_id, cost, cached in ((1, "0", 0), (2, "0.001", 0), (3, "0", 1)):
            conn.execute(text(
                "INSERT INTO decisions (id, cycle_id, symbol, context, final_action, model_output, "
                "prompt_version, prompt_hash, model, tokens, cost, cached) "
                "VALUES (:id, 1, 'WMT', 'candidate', 'enter', :output, 'decision.v1', 'hash', "
                "'fake', 30, :cost, :cached)"
            ), {
                "id": decision_id, "cost": cost, "cached": cached,
                "output": '{"stop_price": "95"}',
            })
        conn.execute(text(
            "INSERT INTO positions (symbol, origin, pending_ack, entry_decision_id, qty, "
            "avg_entry_price, current_stop_price, opened_at) VALUES "
            "('WMT', 'agent', 0, 1, 16, 100, 99, '2026-10-22 15:47:00')"
        ))
        before = conn.execute(text("SELECT id, cost FROM decisions ORDER BY id")).all()
    _upgrade(db, "head", monkeypatch)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT initial_stop FROM positions")).scalar_one() == 95
        assert conn.execute(text("SELECT id, cost FROM decisions ORDER BY id")).all() == before
        sources = conn.execute(
            text("SELECT cost_source FROM decisions ORDER BY id")
        ).scalars().all()
        assert sources == ["legacy", "priced", "cached"]
    engine.dispose()


def test_head_schema_has_earnings_hour_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "fresh.db"
    _upgrade(db, "head", monkeypatch)
    engine = create_engine(f"sqlite:///{db}")
    columns = {c["name"]: c for c in inspect(engine).get_columns("earnings")}
    assert "hour_raw" in columns
    assert columns["hour_raw"]["nullable"] is False


def test_earnings_migration_backfills_and_normalises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "migrated.db"
    _upgrade(db, FIRST_REVISION, monkeypatch)
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO earnings (symbol, date, time_of_day, fetched_at) "
                "VALUES ('AAPL', '2026-10-30', 'bmo', '2026-10-15 12:00:00'), "
                "('MSFT', '2026-10-31', 'dmh', '2026-10-15 12:00:00'), "
                "('XOM', '2026-11-01', 'weird', '2026-10-15 12:00:00')"
            )
        )
    _upgrade(db, "head", monkeypatch)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT symbol, time_of_day || '|' || hour_raw FROM earnings")
        ).all()
    rows: dict[str, str] = dict(result)
    assert rows["AAPL"] == "before_open|bmo"
    assert rows["MSFT"] == "during_market|dmh"
    assert rows["XOM"] == "unknown|weird"
