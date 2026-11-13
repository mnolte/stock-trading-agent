"""Item 082: every loosening asks "Confirm it is you".

The direction map lives in the backend (``settings_service.DIRECTIONS``);
``classify`` turns a save's diff into tighter / looser / neutral and
``confirm_fields`` adds the rules that sit outside the map. These tests
cover the map's coverage of ``config.example.yaml``, every direction
kind both ways, and the 403 ``confirm_required`` answers through the
API with ``SA_AUTH=required``.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import yaml
from app.settings_service import (
    DIRECTION_SECTIONS,
    DIRECTIONS,
    _classify_change,
    _direction_for,
    classify,
    confirm_fields,
)
from fastapi.testclient import TestClient

from tests.helpers import auth_app, csrf_headers, sign_in


@pytest.fixture
def app_auth(monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    yield auth_app(monkeypatch)


def _set(tree: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Return a copy of ``tree`` with the dotted ``path`` set."""
    import copy

    out = copy.deepcopy(tree)
    node = out
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return out


def _leaves(tree: dict[str, Any], prefix: str = "") -> list[str]:
    """Dotted paths of every non-dict leaf, like ``classify`` sees them."""
    out: list[str] = []
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.extend(_leaves(value, path))
        else:
            out.append(path)
    return out


# ---------------------------------------------------------------------
# the map covers the config


def test_every_config_leaf_has_a_direction(repo_root: Path) -> None:
    """Every leaf in the trading sections of config.example.yaml
    resolves to a DIRECTIONS entry — exact or through a covering
    ancestor. An unmapped key would count as looser at save time; this
    test fails first so the gap is a deliberate "neutral" with a
    comment, never an accident."""
    config = yaml.safe_load(
        (repo_root / "backend" / "config.example.yaml").read_text()
    )
    assert isinstance(config, dict)
    missing: list[str] = []
    for section in DIRECTION_SECTIONS:
        body = config.get(section)
        assert isinstance(body, dict), f"config.example.yaml: {section} missing"
        for path in _leaves(body):
            dotted = f"{section}.{path}"
            if _direction_for(dotted) is None:
                missing.append(dotted)
    assert missing == [], f"no DIRECTIONS entry for: {missing}"


def test_directions_only_cover_the_trading_sections() -> None:
    for key in DIRECTIONS:
        assert key.partition(".")[0] in DIRECTION_SECTIONS, key


# ---------------------------------------------------------------------
# classify: one test per direction kind, both ways


@pytest.mark.parametrize(
    ("path", "old", "new", "expected"),
    [
        # up — looser when the number goes up
        ("risk.max_open_positions", 4, 5, "looser"),
        ("risk.max_open_positions", 4, 3, "tighter"),
        ("risk.earnings_blackout_days", 3, 1, "looser"),  # down kind
        ("risk.earnings_blackout_days", 3, 5, "tighter"),
        # down — looser when the number goes down
        ("decision.min_confidence_enter", 0.55, 0.5, "looser"),
        ("decision.min_confidence_enter", 0.55, 0.6, "tighter"),
        ("decision.min_reward_risk", 1.5, 1.2, "looser"),
        ("decision.min_reward_risk", 1.5, 1.8, "tighter"),
        # on / off
        ("risk.hold_through_earnings", False, True, "looser"),
        ("risk.hold_through_earnings", True, False, "tighter"),
        ("rules.require_daily_above_sma50", True, False, "looser"),
        ("rules.require_daily_above_sma50", False, True, "tighter"),
        ("events.fomc.enabled", True, False, "looser"),
        ("decision.v5.trend_gate", True, False, "looser"),
        # longer / shorter — lists
        ("decision.v5.trend_gate_regimes", ["up"], ["up", "sideways"], "looser"),
        ("decision.v5.trend_gate_regimes", ["up", "sideways"], ["up"], "tighter"),
        ("regime.block_entries_in", ["down_high"], [], "looser"),
        ("regime.block_entries_in", [], ["down_high"], "tighter"),
        ("universe.symbols", ["SPY"], ["SPY", "QQQ"], "looser"),
        ("schedule.cycles", ["10:46"], ["10:46", "11:46"], "looser"),
        ("schedule.position_checks", ["09:46", "10:01"], ["09:46"], "looser"),
        # neutral — never asks, either way
        ("execution.time_in_force", "gtc", "ioc", "neutral"),
        ("decision.model", "gpt-4.1-mini", "gpt-4o-mini", "neutral"),
        ("selection.mode", "rank", "expected_profit", "neutral"),
    ],
)
def test_classify_direction_kinds(
    path: str, old: Any, new: Any, expected: str
) -> None:
    old_tree = _set({}, path, old)
    new_tree = _set({}, path, new)
    changes = classify(old_tree, new_tree)
    assert len(changes) == 1, changes
    assert changes[0]["key"] == path
    assert changes[0]["direction"] == expected


def test_classify_unmapped_key_fails_closed() -> None:
    """A covered-section key with no DIRECTIONS entry counts as
    looser — the map can't silently miss a new setting."""
    changes = classify(
        _set({}, "risk.brand_new_knob", 1), _set({}, "risk.brand_new_knob", 2)
    )
    assert changes[0]["direction"] == "looser"


def test_classify_ignores_uncovered_sections() -> None:
    """Sections outside the trading list never produce a change —
    reviews, ask, readiness and friends don't ask."""
    changes = classify(
        _set({}, "reviews.monthly_cost_cap_usd", 2),
        _set({}, "reviews.monthly_cost_cap_usd", 50),
    )
    assert changes == []


def test_classify_wider_kind() -> None:
    """``wider``: a [low, high] pair — widening is looser, narrowing
    tighter, a shift or a mixed move neutral. No config leaf uses it
    today; the kind stays for the next range setting."""
    assert _classify_change("wider", [40, 70], [30, 80]) == "looser"
    assert _classify_change("wider", [40, 70], [45, 65]) == "tighter"
    assert _classify_change("wider", [40, 70], [40, 80]) == "looser"
    assert _classify_change("wider", [40, 70], [40, 70]) == "neutral"
    assert _classify_change("wider", [40, 70], [45, 75]) == "neutral"


def test_classify_ancestor_entry_covers_nested_keys() -> None:
    """``universe.names`` is one neutral setting; its per-ticker leaves
    resolve through the ancestor entry."""
    changes = classify(
        _set({}, "universe.names.SPY", "SPDR"),
        _set({}, "universe.names.SPY", "S&P 500"),
    )
    assert changes[0]["direction"] == "neutral"


# ---------------------------------------------------------------------
# confirm_fields: the rules outside the direction map


def test_confirm_fields_lists_only_looser_changes() -> None:
    """A mixed save reports only the changes that need the
    confirmation — tighter and neutral changes stay out."""
    old = {
        "risk": {"daily_loss_limit_pct": 2.0, "max_open_positions": 4},
        "decision": {"min_confidence_enter": 0.55},
    }
    new = {
        "risk": {"daily_loss_limit_pct": 3.0, "max_open_positions": 2},
        "decision": {"min_confidence_enter": 0.5},
    }
    fields = confirm_fields(old, new)
    keys = {f["key"] for f in fields}
    assert keys == {"risk.daily_loss_limit_pct", "decision.min_confidence_enter"}
    by_key = {f["key"]: f for f in fields}
    assert by_key["risk.daily_loss_limit_pct"]["label"] == "Daily loss limit"
    assert by_key["risk.daily_loss_limit_pct"]["old"] == "2.0"
    assert by_key["risk.daily_loss_limit_pct"]["new"] == "3.0"


def test_confirm_fields_supervised_to_automatic() -> None:
    old = {"execution": {"mode": "supervised"}}
    new = {"execution": {"mode": "automatic"}}
    fields = confirm_fields(old, new)
    assert [f["key"] for f in fields] == ["execution.mode"]
    # The other way never asks.
    assert confirm_fields(new, old) == []


def test_confirm_fields_universe_mode() -> None:
    old = {"universe": {"mode": "fixed"}}
    new = {"universe": {"mode": "screened"}}
    assert [f["key"] for f in confirm_fields(old, new)] == ["universe.mode"]
    assert confirm_fields(new, old) == []


def test_confirm_fields_decision_model_and_prompt() -> None:
    old = {"decision": {"model": "gpt-4.1-mini", "prompt": "decision.v1"}}
    new = {"decision": {"model": "gpt-4o-mini", "prompt": "decision.v1"}}
    fields = confirm_fields(old, new)
    assert [f["key"] for f in fields] == ["decision.model"]
    new["decision"]["prompt"] = "decision.v2"
    fields = confirm_fields(old, new)
    assert {f["key"] for f in fields} == {"decision.model", "decision.prompt"}


def test_confirm_fields_notification_channel_loss() -> None:
    old = {
        "notifications": {
            "events": {"kill_switch": ["browser", "email"], "trade": []}
        }
    }
    lost = {
        "notifications": {
            "events": {"kill_switch": ["browser"], "trade": []}
        }
    }
    fields = confirm_fields(old, lost)
    assert [f["key"] for f in fields] == ["notifications.events.kill_switch"]
    # Gaining a channel never asks.
    assert confirm_fields(lost, old) == []


def test_confirm_fields_quiet_hours_longer() -> None:
    old = {"notifications": {"quiet_hours": {"start": "23:00", "end": "08:00"}}}
    longer = {"notifications": {"quiet_hours": {"start": "22:00", "end": "09:00"}}}
    shorter = {"notifications": {"quiet_hours": {"start": "23:30", "end": "07:30"}}}
    same_span = {
        "notifications": {"quiet_hours": {"start": "22:00", "end": "07:00"}}
    }
    fields = confirm_fields(old, longer)
    assert [f["key"] for f in fields] == ["notifications.quiet_hours"]
    assert confirm_fields(old, shorter) == []
    # Same length, different window: not longer, doesn't ask.
    assert confirm_fields(old, same_span) == []


# ---------------------------------------------------------------------
# through the API with SA_AUTH=required


def _save(client: TestClient, settings: dict[str, Any]) -> Any:
    return client.post(
        "/api/settings",
        json={"settings": settings, "reason": "confirm test"},
        headers=csrf_headers(client),
    )


def _confirm(client: TestClient) -> None:
    r = client.post(
        "/api/auth/confirm",
        json={"secret": "a fourteen char password"},
        headers=csrf_headers(client),
    )
    assert r.status_code == 200, r.text


def test_api_loosening_requires_confirm_with_fields(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        r = _save(client, {"decision": {"min_confidence_enter": 0.5}})
        assert r.status_code == 403, r.text
        error = r.json()["error"]
        assert error["code"] == "confirm_required"
        fields = error["fields"]
        assert len(fields) == 1
        assert fields[0]["key"] == "decision.min_confidence_enter"
        assert fields[0]["label"] == "Min confidence to enter"
        # Tightening the same gate never asks.
        r = _save(client, {"decision": {"min_confidence_enter": 0.6}})
        assert r.status_code == 200, r.text


def test_api_mixed_save_lists_only_looser_fields(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        r = _save(
            client,
            {
                "risk": {
                    "daily_loss_limit_pct": 3,  # looser
                    "max_open_positions": 2,  # tighter
                },
                "rules": {"rsi_min": 45},  # tighter (higher floor)
            },
        )
        assert r.status_code == 403, r.text
        keys = {f["key"] for f in r.json()["error"]["fields"]}
        assert keys == {"risk.daily_loss_limit_pct"}


def test_api_supervised_to_automatic_asks(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        # automatic → supervised tightens: no confirmation needed.
        r = _save(client, {"execution": {"mode": "supervised"}})
        assert r.status_code == 200, r.text
        # supervised → automatic loosens: asks.
        r = _save(client, {"execution": {"mode": "automatic"}})
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "confirm_required"
        keys = {f["key"] for f in r.json()["error"]["fields"]}
        assert keys == {"execution.mode"}
        # Confirm, then the same save goes through.
        _confirm(client)
        r = _save(client, {"execution": {"mode": "automatic"}})
        assert r.status_code == 200, r.text
        # automatic → supervised never asks — a fresh sign-in has no
        # confirmation, and the save still answers 200.
        sign_in(client, secret, remember=True)
        r = _save(client, {"execution": {"mode": "supervised"}})
        assert r.status_code == 200, r.text


def test_api_losing_alert_channel_asks(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        # kill_switch loses its email channel: asks.
        r = _save(
            client, {"notifications": {"events": {"kill_switch": ["browser"]}}}
        )
        assert r.status_code == 403, r.text
        keys = {f["key"] for f in r.json()["error"]["fields"]}
        assert keys == {"notifications.events.kill_switch"}
        _confirm(client)
        r = _save(
            client, {"notifications": {"events": {"kill_switch": ["browser"]}}}
        )
        assert r.status_code == 200, r.text
        # Adding the channel back never asks — even on a session whose
        # confirmation has lapsed.
        from datetime import timedelta

        app.state.clock.advance(timedelta(minutes=6))
        r = _save(
            client,
            {"notifications": {"events": {"kill_switch": ["browser", "email"]}}},
        )
        assert r.status_code == 200, r.text


def test_api_quiet_hours_longer_asks(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        # Default 23:00–08:00 is 9 h; 22:00–09:00 is 11 h — longer asks.
        r = _save(
            client,
            {"notifications": {"quiet_hours": {"start": "22:00", "end": "09:00"}}},
        )
        assert r.status_code == 403, r.text
        keys = {f["key"] for f in r.json()["error"]["fields"]}
        assert keys == {"notifications.quiet_hours"}
        _confirm(client)
        r = _save(
            client,
            {"notifications": {"quiet_hours": {"start": "22:00", "end": "09:00"}}},
        )
        assert r.status_code == 200, r.text


def test_api_settings_returns_directions(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        body = client.get("/api/settings").json()
        directions = body["directions"]
        assert directions["decision.min_confidence_enter"] == "down"
        assert directions["risk.hold_through_earnings"] == "on"
        assert directions["execution.mode"] == "neutral"
        assert "risk.risk_per_trade_pct" in directions


def test_auth_off_nothing_asks(client: TestClient) -> None:
    """SA_AUTH=off: a loosening save answers 200 with no confirmation —
    unchanged behaviour for local dev."""
    r = client.post(
        "/api/settings",
        json={
            "settings": {"decision": {"min_confidence_enter": 0.5}},
            "reason": "local",
        },
    )
    assert r.status_code == 200, r.text
