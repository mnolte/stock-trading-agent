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
