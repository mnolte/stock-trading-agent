from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["mode"] == "simulated"


def test_header_is_paper(client: TestClient) -> None:
    response = client.get("/api/header")
    assert response.status_code == 200
    data = response.json()
    assert data["paper"] is True
    assert data["mode"] == "simulated"
    assert data["session"]["state"] in {"open", "closed", "last_cycle_done", "early_close"}
