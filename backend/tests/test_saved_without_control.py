from __future__ import annotations

import copy
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import SettingsVersion
from app.settings_service import (
    EDITABLE,
    SUBSPECS,
    control_keys,
    has_control,
    refresh_saved_without_control,
    saved_without_control,
)
from fastapi.testclient import TestClient


def version(number: int, settings: dict[str, Any]) -> SettingsVersion:
    return SettingsVersion(
        version=number,
        settings=settings,
        changed_at=datetime(2026, 11, number, tzinfo=UTC),
        reason="test",
    )


def test_comparison_filters_and_numeric_equality() -> None:
    layers: dict[str, Any] = {
        "concurrency": {"max_requests": 20},
        "risk": {"max_open_positions": 4},
        "notifications": {"email": {"smtp_host": "new.example.test"}},
        "test": {"equal": 10, "decimal": Decimal("10.0")},
        "models": {"model": {"price": 2}},
        "universe": {"sectors": {"SPY": "ETF"}, "names": {"SPY": "SPY"}},
        "secrets": {"password": "fake-new"},
        "ops": {"heartbeat_url": "fake-new"},
    }
    saved = copy.deepcopy(layers)
    saved["concurrency"]["max_requests"] = 10
    saved["risk"]["max_open_positions"] = 2
    saved["notifications"]["email"]["smtp_host"] = "old.example.test"
    saved["test"] = {"equal": 10.0, "decimal": 10}
    saved["models"]["model"]["price"] = 1
    saved["universe"] = {"sectors": {"SPY": "old"}, "names": {"SPY": "old"}}
    saved["secrets"]["password"] = "fake-old"
    saved["ops"]["heartbeat_url"] = "fake-old"
    saved["retired"] = {"key": 1}
    row = version(1, saved)
    result = saved_without_control(layers, row, [row])
    assert [item["key"] for item in result] == ["concurrency.max_requests"]
    assert result[0]["label"] == "concurrency.max_requests"
    assert result[0]["direction"] == "looser"
    assert saved_without_control(layers, None, []) == []


def test_since_is_unbroken_run_ending_at_active() -> None:
    rows = [version(i, {"concurrency": {"max_requests": n}}) for i, n in enumerate([20, 10, 10], 1)]
    result = saved_without_control({"concurrency": {"max_requests": 20}}, rows[-1], rows)
    assert result[0]["since_version"] == 2
    assert result[0]["since_at"].startswith("2026-11-02")
    rows.append(version(4, {"concurrency": {"max_requests": 20}}))
    result = saved_without_control({"concurrency": {"max_requests": 20}}, rows[2], rows)
    assert result[0]["since_version"] == 2


def test_last_cycle_control_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.settings_service as service

    layers = {"schedule": {"last_cycle_no_entries": False}}
    row = version(1, {"schedule": {"last_cycle_no_entries": True}})
    assert saved_without_control(layers, row, [row]) == []
    monkeypatch.setattr(service, "control_keys", lambda: set())
    assert saved_without_control(layers, row, [row])[0]["key"] == "schedule.last_cycle_no_entries"


def test_every_editable_leaf_has_a_control() -> None:
    config = get_config()
    for section, keys in EDITABLE.items():
        for key, (kind, _, _) in keys.items():
            path = f"{section}.{key}"
            if kind == "submap":
                leaves = [f"{path}.{leaf}" for leaf in SUBSPECS[(section, key)]]
            elif kind in {"review_models", "review_prompts", "quiet_hours", "notify_events"}:
                leaves = [f"{path}.{leaf}" for leaf in config.get(section, key)]
            else:
                leaves = [path]
            assert all(has_control(leaf) for leaf in leaves), leaves
    assert "schedule.cycles" not in control_keys()
    assert not has_control("notifications.events.trade.extra")


def seed_saved(path: str, value: Any) -> dict[str, Any]:
    config = get_config()
    tree = config.effective()
    node = tree
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    from app.settings_service import _json_safe

    with get_session_maker()() as session:
        session.add(version(1, _json_safe(tree)))
        session.commit()
        config.apply_saved(tree, 1)
        refresh_saved_without_control(config, session)
    return tree


def test_use_config_changes_only_one_key_and_clears_attention(client: TestClient) -> None:
    tree = seed_saved("concurrency.max_requests", 10)
    body = client.get("/api/settings").json()
    assert body["saved_without_control"][0]["saved"] == 10
    attention = client.get("/api/attention").json()
    assert any(item["kind"] == "saved_without_control" for item in attention["items"])
    result = client.post(
        "/api/settings/use-config",
        json={
            "key": "concurrency.max_requests",
            "reason": "take config",
        },
    )
    assert result.status_code == 200, result.text
    assert "restart" in result.json()["effective_from"]
    tree["concurrency"]["max_requests"] = get_config().yaml["concurrency"]["max_requests"]
    with get_session_maker()() as session:
        saved = session.query(SettingsVersion).filter_by(version=2).one()
        from app.settings_service import _json_safe

        assert saved.settings == _json_safe(tree)
        assert saved.reason == "take config"
    assert client.get("/api/settings").json()["saved_without_control"] == []
    assert not any(
        item["kind"] == "saved_without_control"
        for item in client.get("/api/attention").json()["items"]
    )


@pytest.mark.parametrize(
    "body",
    [
        {"key": "not.a.key", "reason": "test"},
        {"key": "risk.max_open_positions", "reason": "test"},
        {"key": "concurrency.max_requests"},
        {"key": "concurrency.max_requests", "reason": " "},
    ],
)
def test_use_config_refuses_invalid_request(client: TestClient, body: dict[str, Any]) -> None:
    seed_saved("concurrency.max_requests", 10)
    assert client.post("/api/settings/use-config", json=body).status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "schedule.cycles",
        "readiness.ratio_min_trades",
        "readiness.error_auto_resolve_minutes",
    ],
)
def test_api_only_keys_are_no_longer_editable(client: TestClient, path: str) -> None:
    section, key = path.split(".")
    value = get_config().get(section, key)
    response = client.post(
        "/api/settings",
        json={
            "settings": {section: {key: value}},
            "reason": "test",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("path", "saved", "expected"),
    [
        ("execution.entry_cutoff", "15:55", 200),
        ("execution.entry_cutoff", "15:40", 403),
        ("reviews.max_words.briefing", 80, 403),
    ],
)
def test_use_config_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    saved: Any,
    expected: int,
) -> None:
    from tests.helpers import auth_app, csrf_headers, sign_in

    app, secret, _ = auth_app(monkeypatch)
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        seed_saved(path, saved)
        response = client.post(
            "/api/settings/use-config",
            json={
                "key": path,
                "reason": "take config",
            },
            headers=csrf_headers(client),
        )
        assert response.status_code == expected, response.text
        if expected == 403:
            assert response.json()["error"]["code"] == "confirm_required"
            confirm = client.post(
                "/api/auth/confirm",
                json={
                    "secret": "a fourteen char password",
                },
                headers=csrf_headers(client),
            )
            assert confirm.status_code == 200
            response = client.post(
                "/api/settings/use-config",
                json={
                    "key": path,
                    "reason": "take config",
                },
                headers=csrf_headers(client),
            )
            assert response.status_code == 200


def test_startup_warning_names_only(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    from app.api.main import load_saved_settings

    seed_saved("concurrency.max_requests", 123456)
    caplog.clear()
    load_saved_settings(get_config())
    warnings = [record.message for record in caplog.records
                if record.message.startswith("saved values differ from config:")]
    assert warnings == ["saved values differ from config: concurrency.max_requests"]
    assert "123456" not in " ".join(warnings)


def test_use_config_preserves_an_unrelated_pending_save(client: TestClient) -> None:
    seed_saved("concurrency.max_requests", 10)
    saved = client.post("/api/settings", json={
        "settings": {"risk": {"max_open_positions": 2}}, "reason": "tighter",
    })
    assert saved.status_code == 200
    assert client.get("/api/settings").json()["saved_without_control"]
    result = client.post("/api/settings/use-config", json={
        "key": "concurrency.max_requests", "reason": "take config",
    })
    assert result.status_code == 200
    pending = get_config().pending()
    assert pending is not None
    assert pending["risk"]["max_open_positions"] == 2
    assert pending["concurrency"]["max_requests"] == (
        get_config().yaml["concurrency"]["max_requests"]
    )
