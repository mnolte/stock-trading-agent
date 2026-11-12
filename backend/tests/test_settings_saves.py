"""Settings saves — repository scan of 31 October.

``validate_settings`` lost two branches in PR 29: ``reviews.models`` was
overwritten by ``_clean_quiet_hours`` (so every save 422'd on a quiet-hours
message) and ``notifications.quiet_hours`` had no branch left (it fell
through to the numeric cleaner). These tests save through the API the way
the Settings page does, and walk every key the form can send so a dropped
branch can't silently disappear again.
"""

from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from app.db.base import get_session_maker
from app.db.models import Strategy
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean_settings() -> Generator[None, None, None]:
    """Keep saved versions and the pending tree out of later tests."""
    yield
    from app.config import get_config
    from app.db.base import get_session_maker
    from app.db.models import SettingsVersion

    config = get_config()
    config._pending = None
    config.pending_version = None
    config.reschedule_cycles = False
    with get_session_maker()() as session:
        session.query(SettingsVersion).delete()
        session.commit()


def _save(client: TestClient, settings: dict[str, Any]) -> Any:
    return client.post(
        "/api/settings",
        json={"settings": settings, "reason": "scan fix test"},
    )


def test_save_review_models(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    models = dict(body["settings"]["reviews"]["models"])
    other = next(
        option["id"]
        for option in body["model_options"]
        if option["id"] != models["weekly"]
    )
    models["weekly"] = other

    resp = _save(client, {"reviews": {"models": models}})

    assert resp.status_code == 200, resp.text
    assert any(
        change.startswith("Weekly review model:")
        for change in resp.json()["changes"]
    )
    # The saved value lands in the pending tree, not a quiet-hours dict.
    pending = client.get("/api/settings").json()["pending"]
    assert pending["reviews.models.weekly"] == other


def test_save_review_models_rejects_unknown_task(client: TestClient) -> None:
    resp = _save(client, {"reviews": {"models": {"nightly": "gpt-4o-mini"}}})

    assert resp.status_code == 422
    assert "reviews.models.nightly: unknown review task" in resp.text


def test_save_review_models_rejects_empty(client: TestClient) -> None:
    resp = _save(client, {"reviews": {"models": {"weekly": "   "}}})

    assert resp.status_code == 422
    assert "Weekly review model: must not be empty" in resp.text


def test_save_quiet_hours(client: TestClient) -> None:
    resp = _save(
        client,
        {"notifications": {"quiet_hours": {"start": "22:30", "end": "07:15"}}},
    )

    assert resp.status_code == 200, resp.text
    changes = resp.json()["changes"]
    assert "Quiet hours from: 23:00 → 22:30" in changes
    assert "Quiet hours to: 08:00 → 07:15" in changes
    pending = client.get("/api/settings").json()["pending"]
    assert pending["notifications.quiet_hours.start"] == "22:30"
    assert pending["notifications.quiet_hours.end"] == "07:15"


def test_save_quiet_hours_rejects_bad_time(client: TestClient) -> None:
    resp = _save(
        client,
        {"notifications": {"quiet_hours": {"start": "noon", "end": "07:00"}}},
    )

    assert resp.status_code == 422
    assert "not an HH:MM time" in resp.text


# Form rows the page renders read-only — they can never land in a save
# body. ``universe.sectors`` travels with ``universe.symbols`` (the page
# sends them together), so it is covered by the symbols save below.
READ_ONLY_FORM_KEYS = {
    ("performance", "starting_equity"),
    ("universe", "allowed_sectors"),
    ("universe", "sectors"),
}


def test_settings_form_covers_every_editable_section(client: TestClient) -> None:
    """Every section ``validate_settings`` accepts must be in the form.

    ``ask`` was editable on save but missing from ``_settings_form``: the
    page still offered its controls, and an Ask edit crashed the diff on a
    section the GET never sent. ``ops`` is intentionally absent — its
    heartbeat URL is saved from the Server section, not the form tree.
    """
    from app.settings_service import EDITABLE

    form = client.get("/api/settings").json()["settings"]
    for section, keys in EDITABLE.items():
        if section == "ops":
            continue
        assert section in form, f"{section}: editable but not in the form"
        for key in keys:
            assert key in form[section], f"{section}.{key}: not in the form"


def test_every_settings_form_key_round_trips(client: TestClient) -> None:
    """Every key the Settings page can put in a save body must validate.

    Each key goes up the way the page sends it — its current value, which
    is always a legal save. A kind without a ``validate_settings`` branch
    (the quiet-hours bug) falls through to the numeric cleaner and 422s.
    """
    form = client.get("/api/settings").json()["settings"]
    bodies: list[tuple[str, dict[str, Any]]] = []
    for section in sorted(form):
        for key in sorted(form[section]):
            path = f"{section}.{key}"
            if (section, key) in READ_ONLY_FORM_KEYS:
                continue
            if (section, key) == ("universe", "symbols"):
                # The page always sends the sector map with the list.
                bodies.append(
                    (
                        path,
                        {
                            "universe": {
                                "symbols": form["universe"]["symbols"],
                                "sectors": form["universe"]["sectors"],
                            }
                        },
                    )
                )
            elif (section, key) == ("universe", "screen"):
                # The page sends one screen leaf per save.
                for leaf, value in sorted(form["universe"]["screen"].items()):
                    bodies.append(
                        (
                            f"{path}.{leaf}",
                            {"universe": {"screen": {leaf: value}}},
                        )
                    )
            else:
                bodies.append((path, {section: {key: form[section][key]}}))

    assert bodies, "the settings form exposed no keys"
    for path, settings in bodies:
        resp = _save(client, settings)
        assert resp.status_code == 200, f"{path}: {resp.text}"


def test_prompt_options_marks_versions_a_shadow_runs(
    client: TestClient,
) -> None:
    """The decision-prompt picker marks a version an active or paused
    shadow uses (design/screens/Settings.dc.html); archived shadows do
    not mark it."""
    with get_session_maker()() as session:
        for name, version, status in (
            ("E · EV shadow", "decision.v5", "active"),
            ("F · Floor shadow", "decision.v5", "paused"),
            ("G · Gone shadow", "decision.v4", "archived"),
        ):
            session.add(
                Strategy(
                    name=name,
                    kind="llm",
                    prompt_version=version,
                    model="gpt-4o-mini",
                    temperature=0.0,
                    status=status,
                    created_at=datetime(2026, 10, 15, 18, 0),
                )
            )
        session.commit()

    body = client.get("/api/settings").json()
    options = {
        p["version"]: p for p in body["prompt_options"]["decision"]
    }
    assert options["decision.v5"]["shadows"] == ["E", "F"]
    assert options["decision.v4"]["shadows"] == []
    assert options["decision.v3"]["shadows"] == []


def test_settings_lists_active_shadow_overrides(client: TestClient) -> None:
    """Item 069: rows can show "Shadow A uses 45–65" — only values an
    active shadow runs differently from live, never archived shadows."""
    with get_session_maker()() as session:
        session.add(
            Strategy(
                name="Shadow A",
                kind="llm",
                prompt_version="decision.v1",
                model="gpt-4o-mini",
                temperature=0.0,
                status="active",
                overrides={"rsi_min": 45, "rsi_max": 65},
                created_at=datetime(2026, 10, 15, 18, 0),
            )
        )
        session.add(
            Strategy(
                name="Old shadow",
                kind="llm",
                prompt_version="decision.v1",
                model="gpt-4o-mini",
                temperature=0.0,
                status="archived",
                overrides={"rsi_min": 10},
                created_at=datetime(2026, 10, 15, 18, 0),
            )
        )
        session.commit()

    body = client.get("/api/settings").json()
    overrides = body["strategy_overrides"]
    assert overrides["rules.rsi_min"] == [
        {"id": overrides["rules.rsi_min"][0]["id"], "name": "Shadow A", "value": 45}
    ]
    assert overrides["rules.rsi_max"][0]["name"] == "Shadow A"
    assert overrides["rules.rsi_max"][0]["value"] == 65
    # The archived shadow's override is not listed anywhere.
    assert all(
        entry["name"] != "Old shadow"
        for entries in overrides.values()
        for entry in entries
    )
