"""PR 28 step 6 — /api/logs/client: per-address limit, bounded senders,
sanitised fields."""

from __future__ import annotations

from collections.abc import Generator

import app.api.routes as routes
import pytest
from app.config import get_config
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clear_senders() -> Generator[None, None, None]:
    """The per-address buckets are module-global — clear before and
    after so a leaked sender can't spill into another test."""
    routes._client_error_times.clear()
    yield
    routes._client_error_times.clear()


def test_rate_limit_is_per_address_not_tab(client: TestClient) -> None:
    for i in range(10):
        r = client.post("/api/logs/client", json={"tab_id": f"t{i}", "message": "x"})
        assert r.status_code == 200
    r = client.post("/api/logs/client", json={"tab_id": "new-tab", "message": "x"})
    assert r.json()["rate_limited"] is True


def test_senders_table_bounded(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The senders table is capped at security.client_error_senders —
    the oldest entries are evicted when a new sender arrives."""
    get_config().yaml["security"]["client_error_senders"] = 4
    for i in range(4):
        routes._client_error_times[f"10.0.0.{i}"] = [0.0]
    monkeypatch.setattr(
        "app.auth.guard.client_ip", lambda *_a, **_k: "10.0.0.9"
    )
    client.post("/api/logs/client", json={"message": "x"})
    assert len(routes._client_error_times) == 4
    assert "10.0.0.0" not in routes._client_error_times  # oldest evicted
    assert "10.0.0.9" in routes._client_error_times


def test_fields_capped_and_single_lined(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.ERROR, logger="frontend"):
        client.post(
            "/api/logs/client",
            json={
                "message": "a" * 3000 + "\nFAKE-LINE",
                "stack": "s" * 9000,
                "url": "http://x/\ninjected",
            },
        )
    record = caplog.records[-1]
    message = record.getMessage()
    assert "FAKE-LINE" not in message.split("\n")[0]
    assert record.__dict__["context"].get("source") == "frontend"
    assert "\n" not in record.__dict__["context"]["url"]
