"""Startup schema check: refuse to boot on a drifted database."""

import sqlite3
from pathlib import Path

import pytest
from app.config import get_config
from app.db.base import Base, check_schema_matches_models, get_engine


def test_schema_check_passes_on_a_fresh_database() -> None:
    """A database built by the current models passes the check."""
    Base.metadata.create_all(get_engine())
    check_schema_matches_models()


def test_schema_check_names_differing_table_and_column() -> None:
    """A drifted DB fails with the differing table/column in the error."""
    db_path = Path(get_config().env.sa_db_path)
    with sqlite3.connect(db_path) as conn:
        # The shared template already has every table; drift cycles and
        # drop decisions so both failure kinds show in the error.
        conn.execute("DROP TABLE cycles")
        conn.execute("CREATE TABLE cycles (id INTEGER PRIMARY KEY)")
        conn.execute("DROP TABLE decisions")

    with pytest.raises(RuntimeError) as excinfo:
        check_schema_matches_models()

    message = str(excinfo.value)
    assert "cycles.kind missing" in message
    assert "missing table" in message
    assert "may need rebuilding" in message
