"""Settings saves — repository scan of 31 October.

``validate_settings`` lost two branches in PR 29: ``reviews.models`` was
overwritten by ``_clean_quiet_hours`` (so every save 422'd on a quiet-hours
message) and ``notifications.quiet_hours`` had no branch left (it fell
through to the numeric cleaner). These tests save through the API the way
the Settings page does, and walk every key the form can send so a dropped
branch can't silently disappear again.
"""

from collections.abc import Generator
from typing import Any

import pytest
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
