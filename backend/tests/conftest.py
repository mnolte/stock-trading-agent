import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

os.environ.setdefault(
    "SA_DB_PATH", str(Path(tempfile.gettempdir()) / "stock-agent-test.db")
)
# Tests always run against the fakes, whatever .env says — sockets are blocked.
os.environ["MODE"] = "simulated"

import app.config as config_module
from app.api.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own database file so nothing can leak into
    another test or into the next app boot. ``get_config()`` is reset so it
    picks the path up."""
    monkeypatch.setenv("SA_DB_PATH", str(tmp_path / "stock-agent-test.db"))
    monkeypatch.setattr(config_module, "_config", None)


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
