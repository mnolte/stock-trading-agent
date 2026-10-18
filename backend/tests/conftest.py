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
# The real .env never feeds a test: SA_ENV_FILE points at nothing and the
# key variables are scrubbed from the environment before config loads.
os.environ["SA_ENV_FILE"] = str(Path(tempfile.gettempdir()) / "no-such-env-file")
for _var in (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "OPENAI_API_KEY",
    "FINNHUB_API_KEY",
):
    os.environ.pop(_var, None)

import app.config as config_module  # noqa: E402
from app.api.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own database file so nothing can leak into
    another test or into the next app boot. ``get_config()`` is reset so it
    picks the path up."""
    monkeypatch.setenv("SA_DB_PATH", str(tmp_path / "stock-agent-test.db"))
    # secret.key and its mode-600 check need an isolated data dir per test.
    monkeypatch.setenv("SA_DATA_DIR", str(tmp_path / "data"))
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
