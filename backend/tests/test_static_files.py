"""SpaStaticFiles: the SPA fallback must only answer page paths.

A missing file — above all a hashed chunk a stale tab still requests
after a rebuild — must get a real 404, not index.html a script loader
cannot execute. index.html goes out no-cache so a rebuilt GUI is picked
up on the next navigation; hashed assets under /assets/ are immutable.
"""

from pathlib import Path

import pytest
from app.api.main import SpaStaticFiles
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

INDEX = "<html><body>app</body></html>"
CHUNK = "console.log('chunk');"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX)
    (root / "assets" / "index-AAA111.js").write_text(CHUNK)
    return root


@pytest.fixture
def client(dist: Path) -> TestClient:
    app = Starlette(
        routes=[Mount("/", app=SpaStaticFiles(directory=dist, html=True))]
    )
    return TestClient(app)


def test_index_served_no_cache(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["cache-control"] == "no-cache"


def test_unknown_page_path_falls_back_no_cache(client: TestClient) -> None:
    response = client.get("/decisions/5")
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["cache-control"] == "no-cache"


def test_existing_asset_is_immutable(client: TestClient) -> None:
    response = client.get("/assets/index-AAA111.js")
    assert response.status_code == 200
    assert response.text == CHUNK
    assert "immutable" in response.headers["cache-control"]


def test_missing_asset_is_404_not_index(client: TestClient) -> None:
    """The stale-chunk case: after a rebuild a tab asks for a chunk name
    that no longer exists; it must fail, not parse index.html."""
    response = client.get("/assets/index-OLD999.js")
    assert response.status_code == 404
    assert response.text != INDEX


def test_missing_extensionless_path_still_falls_back(
    client: TestClient,
) -> None:
    assert client.get("/settings").text == INDEX
