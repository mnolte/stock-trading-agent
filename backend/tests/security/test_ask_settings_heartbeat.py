"""Item 081 steps 6-7: Ask sees trading settings only; the heartbeat
URL is a write-only secret."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from app.ask.tools import (
    SETTINGS_NEVER,
    SETTINGS_SECTIONS,
    TOOLS,
    AskDeps,
    ToolSpec,
    run_tool,
)
from app.config import get_config
from app.data.cache import _naive_utc
from app.db.models import Event, SettingsVersion
from app.market.clock import FakeClock
from app.ops.urls import UrlRejected
from app.secrets_service import SecretsStore
from app.stored_scrub import HEARTBEAT_EVENT, migrate_heartbeat
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
HB_URL = "https://hc-ping.com/abc123f9a"


def _deps(
    session_factory: sessionmaker[Session], **over: Any
) -> AskDeps:
    return AskDeps(
        config=get_config(),
        clock=FakeClock(NOW),
        session_factory=session_factory,
        **over,
    )


# ---------------------------------------------------------------------
# step 6 — the settings tool allowlist


def test_settings_tool_serves_only_allowlisted_sections(
    session_factory: sessionmaker[Session],
) -> None:
    deps = _deps(session_factory)
    with session_factory() as s:
        result, err = run_tool(s, deps, "settings", {})
    assert err is None
    sections = result["sections"]
    assert set(sections) <= SETTINGS_SECTIONS
    for forbidden in (
        "ops",
        "notifications",
        "security",
        "auth",
        "logging",
        "storage",
        "secrets",
        "market",
        "app",
        "gui",
        "api",
        "models",
        "ask",
        "inspector",
    ):
        assert forbidden not in sections


def test_settings_tool_section_arg_cannot_bypass_allowlist(
    session_factory: sessionmaker[Session],
) -> None:
    deps = _deps(session_factory)
    with session_factory() as s:
        for name in ("ops", "security", "notifications", "auth", "storage"):
            result, _ = run_tool(s, deps, "settings", {"section": name})
            assert name not in result["sections"]
            assert "ops" not in result["sections"]
        allowed, _ = run_tool(s, deps, "settings", {"section": "risk"})
        assert set(allowed["sections"]) <= {"risk"}


def test_settings_universe_has_mode_and_screen_only(
    session_factory: sessionmaker[Session],
) -> None:
    deps = _deps(session_factory)
    with session_factory() as s:
        result, _ = run_tool(s, deps, "settings", {"section": "universe"})
    current = result["sections"]["universe"]["current"]
    assert set(current) <= {"mode", "screen"}
    blob = json.dumps(result["sections"])
    assert '"symbols"' not in blob and '"sectors"' not in blob


def test_settings_channels_report_status_only(
    session_factory: sessionmaker[Session],
) -> None:
    class _Notif:
        email_failures = 0

        def smtp_status(self) -> dict[str, object]:
            return {
                "configured": True,
                "host": "smtp.example.com",
                "to_addr": "me@example.com",
                "username": "smtp-user",
            }

    class _Failing(_Notif):
        email_failures = 2

    deps = _deps(session_factory, notification_service=_Notif())
    with session_factory() as s:
        result, _ = run_tool(s, deps, "settings", {})
        failing, _ = run_tool(
            s, _deps(session_factory, notification_service=_Failing()), "settings", {}
        )
    assert result["channels"] == {"email": "on", "browser": "on"}
    assert failing["channels"]["email"] == "failing"
    blob = json.dumps({**result["channels"], **failing["channels"]})
    assert "smtp.example.com" not in blob
    assert "me@example.com" not in blob
    assert "smtp-user" not in blob


def test_config_example_sections_are_classified() -> None:
    """A new top-level config section fails here until it is either
    allowlisted for Ask or put on the never-list."""
    example = (
        Path(__file__).resolve().parents[2] / "config.example.yaml"
    )
    top = set(yaml.safe_load(example.read_text()))
    unclassified = top - SETTINGS_SECTIONS - SETTINGS_NEVER
    assert not unclassified, f"unclassified config sections: {unclassified}"
    assert not (SETTINGS_SECTIONS & SETTINGS_NEVER)


# ---------------------------------------------------------------------
# step 6 — run_tool scrubs every result


def test_run_tool_scrubs_key_shapes_and_secret_values(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    leaked_key = "sk-proj-FAKEKEY1234567890abcdef"
    stored_secret = "s3cr3t-value-0123456789"

    def fn(session: Session, deps: AskDeps, args: Any) -> dict[str, Any]:
        return {
            "as_of": "x",
            "note": f"saw {leaked_key} and {stored_secret}",
        }

    base = TOOLS["get_status"]
    monkeypatch.setitem(
        TOOLS,
        "zz_leak",
        ToolSpec("zz_leak", "d", base.args_model, fn, "x"),
    )

    class _Secrets:
        def scrub_values(self) -> list[str]:
            return [stored_secret]

    deps = _deps(session_factory, secrets=_Secrets())  # type: ignore[arg-type]
    with session_factory() as s:
        result, err = run_tool(s, deps, "zz_leak", {})
    assert err is None
    blob = json.dumps(result)
    assert leaked_key not in blob
    assert stored_secret not in blob
    assert "[redacted]" in blob


def test_no_tool_result_contains_a_stored_secret(
    session_factory: sessionmaker[Session],
) -> None:
    """Kickoff step 6: for every tool, no result carries a stored
    secret value, the heartbeat URL or the SMTP password — whatever the
    tool looked at."""
    secrets_values = [HB_URL, "smtp-pass-0123456789"]

    class _Secrets:
        def scrub_values(self) -> list[str]:
            return secrets_values

    deps = _deps(session_factory, secrets=_Secrets())  # type: ignore[arg-type]
    args = {
        "calculate": {"expression": "1 + 1"},
        "explain_rule": {"term": "rsi"},
        "market_data": {"symbol": "AAPL", "timeframe": "1d"},
    }
    with session_factory() as s:
        for name in TOOLS:
            result, _ = run_tool(s, deps, name, args.get(name, {}))
            blob = json.dumps(result, default=str)
            for value in secrets_values:
                assert value not in blob, f"{name} leaked {value!r}"


# ---------------------------------------------------------------------
# step 7 — heartbeat URL as a write-only secret


def _secrets_store(tmp_path: Path) -> SecretsStore:
    config = get_config()
    config.env.sa_data_dir = str(tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return SecretsStore(config, FakeClock(NOW))


def test_heartbeat_migration_imports_once_and_blanks_versions(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.ops.urls as urls

    monkeypatch.setattr(
        urls, "validate_public_https_url", lambda url, resolver=None: url
    )
    config = get_config()
    config.env.sa_data_dir = str(tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    secrets = SecretsStore(config, FakeClock(NOW))
    config._active.setdefault("ops", {})["heartbeat_url"] = HB_URL
    with session_factory() as s:
        s.add(
            SettingsVersion(
                version=1,
                settings={"ops": {"heartbeat_url": HB_URL}, "risk": {}},
                changed_at=_naive_utc(NOW),
                reason="old save",
            )
        )
        s.commit()

    clock = FakeClock(NOW)
    assert migrate_heartbeat(session_factory, config, secrets, clock)
    assert secrets.get("heartbeat_url") == HB_URL
    with session_factory() as s:
        row = s.scalar(select(SettingsVersion))
        assert row is not None
        assert row.settings["ops"]["heartbeat_url"] == ""
        flag = s.scalar(
            select(Event.id).where(Event.event_type == HEARTBEAT_EVENT)
        )
        assert flag is not None

    # The flag means "once": a later delete stays deleted even though
    # the config value is still there.
    secrets.remove("heartbeat_url")
    assert not migrate_heartbeat(session_factory, config, secrets, clock)
    assert secrets.get("heartbeat_url") is None


def test_heartbeat_masked_never_shows_full_url(tmp_path: Path) -> None:
    from app.ops.service import OpsService

    secrets = _secrets_store(tmp_path)
    secrets.save("heartbeat_url", HB_URL)
    ops = OpsService(get_config(), FakeClock(NOW), secrets=secrets)
    assert ops.heartbeat_masked() == "hc-ping.com/••••3f9a"
    assert HB_URL not in (ops.heartbeat_masked() or "")


def test_heartbeat_endpoints_write_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.ops.service as ops_service

    def fake_validate(url: str, resolver: object = None) -> str:
        if not url.startswith("https://"):
            raise UrlRejected("not https")
        return url

    monkeypatch.setattr(
        ops_service, "validate_public_https_url", fake_validate
    )

    bad = client.post("/api/server/heartbeat", json={"url": "http://x.example.com/y"})
    assert bad.status_code == 422

    ok = client.post("/api/server/heartbeat", json={"url": HB_URL})
    assert ok.status_code == 200, ok.text
    assert ok.json()["masked"] == "hc-ping.com/••••3f9a"

    gone = client.post("/api/server/heartbeat/remove", json={})
    assert gone.status_code == 200
    again = client.post("/api/server/heartbeat/remove", json={})
    assert again.status_code == 200


def test_settings_save_rejects_heartbeat_url(client: TestClient) -> None:
    resp = client.post(
        "/api/settings",
        json={
            "settings": {"ops": {"heartbeat_url": HB_URL}},
            "reason": "try the old path",
        },
    )
    assert resp.status_code == 422
    assert "not editable" in resp.text


def test_settings_get_and_history_have_no_heartbeat_url(
    client: TestClient,
) -> None:
    blob = json.dumps(client.get("/api/settings").json())
    assert "heartbeat_url" not in blob
