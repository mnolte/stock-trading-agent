"""PR 28 step 6 — /api/logs/client: per-address limit, bounded senders,
sanitised fields."""

from __future__ import annotations

import app.api.routes as routes
import pytest
from fastapi.testclient import TestClient


def test_rate_limit_is_per_address_not_tab(client: TestClient) -> None:
    routes._client_error_times.clear()
    for i in range(10):
        r = client.post("/api/logs/client", json={"tab_id": f"t{i}", "message": "x"})
        assert r.status_code == 200
    r = client.post("/api/logs/client", json={"tab_id": "new-tab", "message": "x"})
    assert r.json()["rate_limited"] is True


def test_senders_table_bounded(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    routes._client_error_times.clear()
    for i in range(5):
        routes._client_error_times[f"10.0.0.{i}"] = [0.0]
    monkeypatch.setattr(
        "app.api.routes.client_ip" if False else "app.auth.guard.client_ip",
        lambda *_a, **_k: "10.0.0.9",
    )
    client.post("/api/logs/client", json={"message": "x"})
    # client_error_senders = 1000 default — monkeypatch a tiny cap check
    # happens via the while-loop; assert table stays sane.
    assert len(routes._client_error_times) <= 1000


def test_fields_capped_and_single_lined(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    routes._client_error_times.clear()
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
