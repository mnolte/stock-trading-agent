"""PR 10 — API keys (docs/SPEC.md, API keys).

Encrypted storage in the secrets table, a write-only API (reads expose
name, last 4, timestamps and test result only), test-before-save, the
pause/resume gate on missing or failing keys, the one-time .env import
and log scrubbing. Every key in this file is a fake; sockets are blocked
by conftest so nothing here can reach a real service.
"""

from __future__ import annotations

import json
import logging
import stat
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from app.api.main import create_app, run_migrations
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import Event, Position, Secret
from app.engine.state import load_execution_state
from app.market.clock import FakeClock, make_clock
from app.secrets_service import (
    FakeKeyTesters,
    KeyTestResult,
    SecretsStore,
    install_secret_scrub_filter,
    scrub,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

pytestmark = pytest.mark.anyio

FAKE_OPENAI = "sk-fake-openai-0123456789abcdef"
FAKE_OPENAI_2 = "sk-fake-openai-zzzzzzzzzzzzzzzz"
FAKE_FINNHUB = "fake-finnhub-key-0123456789abcd"
FAKE_KEY_ID = "PKFAKEKEYID0000000001"
FAKE_SECRET = "fakealpacaSecret0123456789abcdef"
NOW = datetime(2026, 10, 19, 9, 0)


def _store() -> SecretsStore:
    config = get_config()
    return SecretsStore(config, FakeClock(NOW))


def _state(client: TestClient) -> Any:
    """app.state on the test client (TestClient.app is typed as the ASGI
    callable)."""
    return cast(Any, client.app).state


# ----------------------------------------------------------------------
# storage


def test_secret_key_file_created_mode_600() -> None:
    run_migrations()
    _store()
    key_path = Path(get_config().env.sa_data_dir) / "secret.key"
    assert key_path.exists()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_values_are_encrypted_at_rest() -> None:
    """The row holds ciphertext and last4 — never the key itself."""
    run_migrations()
    store = _store()
    store.save("openai", FAKE_OPENAI, "ok")
    with get_session_maker()() as session:
        row = session.execute(
            select(Secret).where(Secret.name == "openai")
        ).scalar_one()
    assert FAKE_OPENAI not in row.value_encrypted
    assert row.value_encrypted.startswith("gAAAA")
    assert row.last4 == FAKE_OPENAI[-4:]
    # …but the store can still hand the value to the clients.
    assert store.get("openai") == FAKE_OPENAI


# ----------------------------------------------------------------------
# write-only API + test before save


def test_keys_never_appear_in_api_responses(client: TestClient) -> None:
    saved = client.post("/api/keys/openai", json={"value": FAKE_OPENAI})
    assert saved.status_code == 200
    body = saved.json()
    assert FAKE_OPENAI not in json.dumps(body)
    assert body["key"]["masked"] == f"sk-••••{FAKE_OPENAI[-4:]}"

    keys = client.get("/api/keys").json()
    assert keys["set_count"] == 1
    assert FAKE_OPENAI not in json.dumps(keys)
    openai = next(k for k in keys["keys"] if k["name"] == "openai")
    assert openai["status"] == "ok"
    assert openai["masked"].endswith(FAKE_OPENAI[-4:])
    assert openai["saved_at"] is not None and openai["tested_at"] is not None

    # …and nowhere else: settings, header and banners stay clean.
    for path in ("/api/settings", "/api/header", "/api/banners"):
        assert FAKE_OPENAI not in client.get(path).text


def test_failed_test_does_not_replace_the_old_key(client: TestClient) -> None:
    assert client.post("/api/keys/openai", json={"value": FAKE_OPENAI}).status_code == 200
    testers = FakeKeyTesters(
        {"openai": KeyTestResult(ok=False, error="invalid key")}
    )
    _state(client).key_testers = testers

    refused = client.post("/api/keys/openai", json={"value": FAKE_OPENAI_2})
    assert refused.status_code == 422
    assert FAKE_OPENAI_2 not in refused.text

    keys = client.get("/api/keys").json()
    openai = next(k for k in keys["keys"] if k["name"] == "openai")
    assert openai["status"] == "ok"  # the old key is still the active one
    assert openai["masked"].endswith(FAKE_OPENAI[-4:])


def test_test_endpoint_rechecks_the_stored_key(client: TestClient) -> None:
    assert client.post("/api/keys/finnhub", json={"value": FAKE_FINNHUB}).status_code == 200
    testers = FakeKeyTesters(
        {"finnhub": KeyTestResult(ok=False, error="403 forbidden")}
    )
    _state(client).key_testers = testers
    checked = client.post("/api/keys/finnhub/test")
    assert checked.status_code == 200
    assert checked.json()["key"]["status"] == "failing"
    assert testers.calls == [("finnhub", (FAKE_FINNHUB,))]


def test_key_events_record_name_last4_and_result_only(client: TestClient) -> None:
    assert client.post("/api/keys/openai", json={"value": FAKE_OPENAI}).status_code == 200
    with get_session_maker()() as session:
        events = session.execute(
            select(Event).where(Event.event_type == "api_key")
        ).scalars().all()
    assert events
    for event in events:
        details = event.details or {}
        assert FAKE_OPENAI not in json.dumps(details)
        assert details["name"] == "openai"
        assert details["last4"] == FAKE_OPENAI[-4:]
        assert details["result"] in {"ok", "failed"}


# ----------------------------------------------------------------------
# Alpaca replace guards


def _open_position() -> None:
    with get_session_maker()() as session:
        session.add(
            Position(
                symbol="AAPL",
                qty=3,
                avg_entry_price=Decimal("200"),
                current_stop_price=Decimal("195"),
                opened_at=datetime(2026, 10, 15, 14, 0),
            )
        )
        session.commit()


def test_alpaca_replace_refused_with_open_positions(client: TestClient) -> None:
    _open_position()
    # The app starts paused, so the pause gate is met — only the open
    # position stands in the way.
    refused = client.post(
        "/api/keys/alpaca",
        json={"key_id": FAKE_KEY_ID, "secret": FAKE_SECRET},
    )
    assert refused.status_code == 422
    assert "close all positions and orders" in refused.json()["detail"]
    assert FAKE_KEY_ID not in refused.text and FAKE_SECRET not in refused.text


def test_alpaca_replace_refused_while_running(client: TestClient) -> None:
    _state(client).exec_state.paused = False
    refused = client.post(
        "/api/keys/alpaca",
        json={"key_id": FAKE_KEY_ID, "secret": FAKE_SECRET},
    )
    assert refused.status_code == 422
    assert "pause the agent" in refused.json()["detail"]


def test_alpaca_replace_saves_both_keys(client: TestClient) -> None:
    # paused at startup, nothing open → the pair tests and saves.
    saved = client.post(
        "/api/keys/alpaca",
        json={"key_id": FAKE_KEY_ID, "secret": FAKE_SECRET},
    )
    assert saved.status_code == 200
    key = saved.json()["key"]
    assert key["status"] == "ok"
    assert key["masked"].startswith("PK••••")
    assert FAKE_KEY_ID not in saved.text and FAKE_SECRET not in saved.text


# ----------------------------------------------------------------------
# resume gate + banner (paper mode, no lifespan → state set by hand)


class _StubExecutor:
    def __init__(self) -> None:
        self.resumed = False

    async def resume_from_pause(self) -> None:
        self.resumed = True

    async def refresh_account(self) -> None:
        return None


def _paper_client() -> tuple[TestClient, _StubExecutor]:
    """A paper-mode app without the lifespan: only the state the routes
    need is installed, so no real client is ever constructed."""
    run_migrations()
    config = get_config()
    config.env.mode = "paper"
    app = create_app()
    clock = make_clock(config)
    app.state.clock = clock
    app.state.exec_state = load_execution_state(clock, config)
    app.state.secrets_store = SecretsStore(config, clock)
    app.state.key_testers = FakeKeyTesters()
    executor = _StubExecutor()
    app.state.executor = executor
    # TestClient without __enter__ never runs the lifespan, so the manual
    # state above is the whole world — no network-capable client exists.
    return TestClient(app), executor


def test_resume_disabled_until_all_keys_pass() -> None:
    client, executor = _paper_client()
    refused = client.post("/api/pause/resume")
    assert refused.status_code == 422
    assert "all three keys" in refused.json()["detail"]
    assert not executor.resumed

    # A kill-switch resume is gated the same way.
    ks = client.post("/api/kill-switch/resume", json={"reason": "back"})
    assert ks.status_code == 422
    assert "all three keys" in ks.json()["detail"]

    # The banner links to Settings and the paused banner carries the
    # blocked flag; the header exposes it for the Resume button.
    banners = client.get("/api/banners").json()["items"]
    missing = next(b for b in banners if b["kind"] == "missing_keys")
    assert "open_settings" in missing["actions"]
    paused = next(b for b in banners if b["kind"] == "paused")
    assert paused["resume_blocked"] is True
    assert client.get("/api/header").json()["resume_blocked"] is True

    assert client.post("/api/keys/openai", json={"value": FAKE_OPENAI}).status_code == 200
    assert client.post("/api/keys/finnhub", json={"value": FAKE_FINNHUB}).status_code == 200
    assert client.post(
        "/api/keys/alpaca",
        json={"key_id": FAKE_KEY_ID, "secret": FAKE_SECRET},
    ).status_code == 200

    resumed = client.post("/api/pause/resume")
    assert resumed.status_code == 200
    assert executor.resumed


def test_missing_keys_banner_gone_once_all_pass() -> None:
    client, _ = _paper_client()
    client.post("/api/keys/openai", json={"value": FAKE_OPENAI})
    client.post("/api/keys/finnhub", json={"value": FAKE_FINNHUB})
    client.post(
        "/api/keys/alpaca",
        json={"key_id": FAKE_KEY_ID, "secret": FAKE_SECRET},
    )
    banners = client.get("/api/banners").json()["items"]
    assert not any(b["kind"] == "missing_keys" for b in banners)
    assert client.get("/api/header").json()["resume_blocked"] is False


# ----------------------------------------------------------------------
# one-time .env import


async def test_env_import_happens_once() -> None:
    """First boot imports env keys (tested first); later boots ignore
    whatever the environment still contains."""
    run_migrations()
    testers = FakeKeyTesters()
    store = _store()
    env = {"openai": FAKE_OPENAI, "finnhub": FAKE_FINNHUB}
    await store.import_env_keys(env, testers)
    assert store.get("openai") == FAKE_OPENAI
    assert store.imported == ["openai", "finnhub"]

    # Second boot (fresh store over the same DB): env values are ignored,
    # even a different value never overwrites the stored one.
    store2 = _store()
    await store2.import_env_keys(
        {"openai": FAKE_OPENAI_2, "finnhub": FAKE_FINNHUB}, testers
    )
    assert store2.imported == []
    assert store2.get("openai") == FAKE_OPENAI


async def test_env_import_tests_before_saving() -> None:
    run_migrations()
    testers = FakeKeyTesters(
        {"openai": KeyTestResult(ok=False, error="bad key")}
    )
    store = _store()
    await store.import_env_keys({"openai": FAKE_OPENAI}, testers)
    # Stored (so a retry with a good key can fix it) but marked failing.
    assert store.get("openai") == FAKE_OPENAI
    assert store.group_status("openai")["status"] == "failing"


# ----------------------------------------------------------------------
# log scrubbing


def test_scrub_strips_key_values() -> None:
    text = scrub(
        f"login failed for {FAKE_OPENAI} (account {FAKE_KEY_ID})",
        [FAKE_OPENAI, FAKE_KEY_ID],
    )
    assert FAKE_OPENAI not in text and FAKE_KEY_ID not in text


def test_log_filter_scrubs_stored_keys() -> None:
    """A message that somehow contains a key never reaches a handler."""
    import io

    run_migrations()
    store = _store()
    store.save("openai", FAKE_OPENAI)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("test.keys.scrub")
    logger.addHandler(handler)
    install_secret_scrub_filter(store.scrub_values)
    try:
        logger.warning("auth failed with %s", FAKE_OPENAI)
        output = stream.getvalue()
        assert FAKE_OPENAI not in output
        assert "[redacted]" in output
    finally:
        logger.removeHandler(handler)


def test_event_details_never_contain_key_values(client: TestClient) -> None:
    """Even a tester error that echoes the key is scrubbed before it is
    stored on the row or the event."""
    testers = FakeKeyTesters(
        {"openai": KeyTestResult(ok=False, error=f"401: {FAKE_OPENAI} denied")}
    )
    _state(client).key_testers = testers
    client.post("/api/keys/openai", json={"value": FAKE_OPENAI})
    store: SecretsStore = _state(client).secrets_store
    with get_session_maker()() as session:
        rows = session.execute(select(Secret)).scalars().all()
        events = session.execute(
            select(Event).where(Event.event_type == "api_key")
        ).scalars().all()
    for row in rows:
        assert row.test_detail is None or FAKE_OPENAI not in row.test_detail
    for event in events:
        assert FAKE_OPENAI not in json.dumps(event.details)
    # A failed candidate is never stored at all.
    assert store.get("openai") is None
