import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

os.environ.setdefault(
    "SA_DB_PATH", str(Path(tempfile.gettempdir()) / "stock-agent-test.db")
)

from app.api.main import create_app
from fastapi.testclient import TestClient


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
