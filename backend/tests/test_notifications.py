"""Notifications service tests (docs/SPEC.md, Notifications).

Fake SMTP, an injected FakeClock, no network. The service is driven
through ``drain`` — the background worker is not started, so a test sees
exactly the rows and sends it asked for. Times: 15 Oct 2026 is inside
CEST, so Amsterdam is UTC+2 (quiet hours 23:00–08:00 = 21:00–06:00 UTC).
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from app.config import AppConfig, get_config
from app.db.base import Base
from app.db.models import Notification, Secret, SmtpSettings
from app.engine.state import record_event, set_event_sink
from app.market.clock import UTC, FakeClock
from app.notifications.mail import FakeMailer
from app.notifications.service import SUBJECT_PREFIX, NotificationService
from app.secrets_service import SecretsStore
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

# 14:00 UTC = 16:00 Amsterdam — normal hours.
NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
# 21:30 UTC = 23:30 Amsterdam — inside quiet hours.
QUIET = datetime(2026, 10, 15, 21, 30, tzinfo=UTC)
# 06:10 UTC = 08:10 Amsterdam — just past the quiet-hours end.
MORNING = datetime(2026, 10, 16, 6, 10, tzinfo=UTC)

PASSWORD = "smtp-secret-98765"

SessionFactory = sessionmaker[Session]


@pytest.fixture
def session_factory(tmp_path: Path) -> SessionFactory:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def config() -> AppConfig:
    return get_config()


@pytest.fixture
def secrets(
    config: AppConfig,
    clock: FakeClock,
    session_factory: SessionFactory,
) -> SecretsStore:
    return SecretsStore(config, clock, session_factory=session_factory)


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def service(
    config: AppConfig,
    clock: FakeClock,
    secrets: SecretsStore,
    mailer: FakeMailer,
    session_factory: SessionFactory,
) -> NotificationService:
    return NotificationService(
        config=config,
        clock=clock,
        secrets=secrets,
        mailer=mailer,
        session_factory=session_factory,
    )


def _rows(session_factory: SessionFactory) -> list[Notification]:
    with session_factory() as session:
        return list(
            session.execute(select(Notification).order_by(Notification.id))
            .scalars()
            .all()
        )


async def _configure_smtp(
    service: NotificationService, secrets: SecretsStore
) -> None:
    """Save working SMTP details; consumes the one save-test send."""
    secrets.save("smtp_password", PASSWORD)
    result = await service.save_smtp(
        host="smtp.test",
        port=587,
        username="agent@test",
        from_addr="agent@test",
        to_addr="marc@test",
    )
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# subject + routing


async def test_subject_format(
    service: NotificationService,
    secrets: SecretsStore,
    mailer: FakeMailer,
    session_factory: SessionFactory,
) -> None:
    """[Stock Trading Agent · PAPER] <event label>: <one line>, same in the email."""
    await _configure_smtp(service, secrets)
    mailer.sent.clear()
    service.notify("kill_switch", "drawdown -8.1% from peak")
    await service.drain()
    (row,) = _rows(session_factory)
    assert (
        row.subject
        == f"{SUBJECT_PREFIX} Kill switch turned on: drawdown -8.1% from peak"
    )
    assert row.subject.startswith("[Stock Trading Agent · PAPER] ")
    (sent,) = mailer.sent
    assert sent.subject == row.subject


async def test_app_channel_is_always_on(
    service: NotificationService,
    session_factory: SessionFactory,
) -> None:
    """An event with no configured channels still lands in the log and
    in-app (the 'app' channel is implied)."""
    service.notify("trade", "opened MSFT: 10 shares @ 420")
    await service.drain()
    (row,) = _rows(session_factory)
    assert row.channels == ["app"]
    assert row.channel_status == {"app": "sent"}


async def test_critical_forces_browser_channel(
    service: NotificationService,
    config: AppConfig,
    session_factory: SessionFactory,
) -> None:
    """Criticals reach the browser even when the row is configured off."""
    config._active["notifications"]["events"]["kill_switch"] = []
    service.notify("kill_switch", "manual")
    await service.drain()
    (row,) = _rows(session_factory)
    assert "browser" in row.channels


async def test_record_event_sink_routes_alerts(
    service: NotificationService,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    """Every existing alert is a durable event; the sink turns the mapped
    event types into notifications."""
    set_event_sink(service.sink)
    try:
        with session_factory() as session:
            record_event(
                session,
                clock,
                level="critical",
                event_type="kill_switch",
                details={"on": True, "reason": "manual"},
            )
            session.commit()
        await service.drain()
        (row,) = _rows(session_factory)
        assert row.event == "kill_switch"
        assert row.severity == "critical"
        assert "manual" in row.subject
    finally:
        set_event_sink(None)


# ---------------------------------------------------------------------------
# criticals, throttle, quiet hours


async def test_critical_bypasses_throttle_and_quiet_hours(
    service: NotificationService,
    secrets: SecretsStore,
    mailer: FakeMailer,
    clock: FakeClock,
    session_factory: SessionFactory,
) -> None:
    """Two identical criticals inside quiet hours both send, immediately."""
    clock.set(QUIET)
    await _configure_smtp(service, secrets)
    mailer.sent.clear()
    service.notify("kill_switch", "manual")
    service.notify("kill_switch", "manual")
    await service.drain()
    rows = _rows(session_factory)
    assert len(rows) == 2
    assert all(r.channel_status.get("email") == "sent" for r in rows)
    assert len(mailer.sent) == 2  # nothing held, nothing throttled


async def test_repeat_limit_is_per_subject(
    service: NotificationService,
    secrets: SecretsStore,
    mailer: FakeMailer,
    clock: FakeClock,
    session_factory: SessionFactory,
) -> None:
    """The 30-minute limit keys on the subject, not the event."""
    await _configure_smtp(service, secrets)
    mailer.sent.clear()
    service.notify("source_failing", "Finnhub failing")
    service.notify("source_failing", "ECB rate failing")
    await service.drain()
    assert len(mailer.sent) == 2  # different subjects both send

    service.notify("source_failing", "Finnhub failing")  # same subject again
    await service.drain()
    assert len(mailer.sent) == 2  # held back
    rows = _rows(session_factory)
    assert rows[-1].channel_status["email"] == "held back"
    assert rows[-1].channel_status["browser"] == "held back"

    clock.advance(timedelta(minutes=31))
    service.notify("source_failing", "Finnhub failing")
    await service.drain()
    assert len(mailer.sent) == 3  # window expired — sends again


async def test_quiet_hours_hold_and_morning_summary(
    service: NotificationService,
    secrets: SecretsStore,
    mailer: FakeMailer,
    clock: FakeClock,
    session_factory: SessionFactory,
) -> None:
    """Non-critical alerts hold during 23:00–08:00 Amsterdam and go out
    as one summary when the window ends."""
    clock.set(QUIET)
    await _configure_smtp(service, secrets)
    mailer.sent.clear()
    service.notify("source_failing", "Finnhub failing")
    service.notify("trade", "opened MSFT: 10 shares @ 420")
    await service.drain()
    assert mailer.sent == []  # nothing sent during quiet hours
    rows = _rows(session_factory)
    assert len(rows) == 2
    held = [r for r in rows if r.event == "source_failing"][0]
    assert held.held_for_quiet is True
    assert held.channel_status["email"] == "quiet hours"
    assert held.channel_status["app"] == "sent"  # in-app always shows

    clock.set(MORNING)
    service.tick()
    await service.drain()
    (summary,) = [
        r for r in _rows(session_factory) if r.event == "quiet_summary"
    ]
    assert (
        summary.subject
        == f"{SUBJECT_PREFIX} Quiet hours summary: 2 held back"
    )
    assert "Finnhub failing" in (summary.body or "")
    assert summary.channel_status["email"] == "sent"
    (sent,) = mailer.sent
    assert sent.subject == summary.subject
    assert all(r.held_for_quiet is False for r in _rows(session_factory))


async def test_tick_inside_quiet_hours_does_nothing(
    service: NotificationService,
    mailer: FakeMailer,
    clock: FakeClock,
    session_factory: SessionFactory,
) -> None:
    clock.set(QUIET)
    service.notify("trade", "opened MSFT: 10 shares @ 420")
    await service.drain()
    service.tick()
    await service.drain()
    assert [r.event for r in _rows(session_factory)] == ["trade"]
    assert mailer.sent == []


# ---------------------------------------------------------------------------
# email failures


async def test_failing_smtp_never_blocks_and_counts_to_banner(
    service: NotificationService,
    secrets: SecretsStore,
    mailer: FakeMailer,
    clock: FakeClock,
    session_factory: SessionFactory,
) -> None:
    """A failing SMTP raises no exception, is logged as failed per
    channel, and the third failure in a row flags the banner."""
    published: list[set[str]] = []
    service._publish = published.append  # observe banner publishes
    await _configure_smtp(service, secrets)
    mailer.fail_with = RuntimeError(f"535 auth failed for {PASSWORD}")
    for _ in range(3):
        service.notify("source_failing", "Finnhub failing")
        clock.advance(timedelta(minutes=31))  # past the repeat limit
        await service.drain()  # must not raise
    assert service.email_failures == 3
    assert {"banners"} in published
    for row in _rows(session_factory):
        assert row.channel_status.get("email") == "failed"
        assert PASSWORD not in (row.error or "")  # scrubbed


async def test_successful_send_resets_failure_count(
    service: NotificationService,
    secrets: SecretsStore,
    mailer: FakeMailer,
    session_factory: SessionFactory,
) -> None:
    await _configure_smtp(service, secrets)
    mailer.fail_with = RuntimeError("boom")
    service.notify("source_failing", "Finnhub failing")
    await service.drain()
    service.notify("source_failing", "ECB rate failing")
    await service.drain()
    assert service.email_failures == 2
    mailer.fail_with = None
    service.notify("source_failing", "OpenAI key failing")
    await service.drain()
    assert service.email_failures == 0


async def test_unconfigured_smtp_is_not_a_failure(
    service: NotificationService,
    session_factory: SessionFactory,
) -> None:
    """Email channels without SMTP details fail soft — no failure count."""
    service.notify("source_failing", "Finnhub failing")
    await service.drain()
    (row,) = _rows(session_factory)
    assert row.channel_status["email"] == "failed"
    assert "not configured" in (row.error or "")
    assert service.email_failures == 0


# ---------------------------------------------------------------------------
# password hygiene


async def test_password_never_appears_anywhere(
    service: NotificationService,
    secrets: SecretsStore,
    mailer: FakeMailer,
    session_factory: SessionFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The SMTP password is write-only: not in rows, sends, status, logs."""
    with caplog.at_level(logging.WARNING):
        await _configure_smtp(service, secrets)
        service.notify("source_failing", "Finnhub failing")
        await service.drain()
        status = service.smtp_status()

    haystack = repr(status)
    for row in _rows(session_factory):
        haystack += repr(
            (
                row.subject,
                row.body,
                row.channel_status,
                row.error,
                row.channels,
            )
        )
    for msg in mailer.sent:
        haystack += repr((msg.subject, msg.body, msg.to_addr))
    for record in caplog.records:
        haystack += record.getMessage()
    assert PASSWORD not in haystack

    with session_factory() as session:
        secret = session.execute(
            select(Secret).where(Secret.name == "smtp_password")
        ).scalar_one()
        assert secret.value_encrypted != PASSWORD  # stored encrypted
        assert PASSWORD not in secret.value_encrypted


async def test_smtp_status_hides_password(
    service: NotificationService,
    secrets: SecretsStore,
    session_factory: SessionFactory,
) -> None:
    await _configure_smtp(service, secrets)
    status = service.smtp_status()
    assert status["configured"] is True
    assert status["password_set"] is True
    assert status["host"] == "smtp.test"
    assert status["test_result"] == "ok"
    assert not [k for k in status if "password" in k and k != "password_set"]


# ---------------------------------------------------------------------------
# SMTP save + test send


async def test_smtp_save_only_after_successful_test(
    service: NotificationService,
    secrets: SecretsStore,
    mailer: FakeMailer,
    session_factory: SessionFactory,
) -> None:
    """A failed test send saves nothing — no row, no password."""
    mailer.fail_with = RuntimeError("connection refused")
    result = await service.save_smtp(
        host="smtp.test",
        port=587,
        username="u",
        from_addr="a@t",
        to_addr="b@t",
        password="whatever",
    )
    assert result["ok"] is False
    assert secrets.get("smtp_password") is None
    with session_factory() as session:
        assert session.execute(select(SmtpSettings)).scalars().all() == []


async def test_send_test_goes_to_every_channel(
    service: NotificationService,
    secrets: SecretsStore,
    mailer: FakeMailer,
    session_factory: SessionFactory,
) -> None:
    await _configure_smtp(service, secrets)
    mailer.sent.clear()
    await service.send_test()
    (row,) = _rows(session_factory)
    assert row.event == "test"
    assert set(row.channels) == {"app", "browser", "email"}
    assert row.channel_status["email"] == "sent"
    (sent,) = mailer.sent
    assert sent.subject == row.subject


# ---------------------------------------------------------------------------
# API surface


async def test_api_never_returns_the_password(client: TestClient) -> None:
    """Save over HTTP, then scan the response and the settings payload."""
    response = client.post(
        "/api/notifications/smtp",
        json={
            "host": "smtp.test",
            "port": 587,
            "username": "agent@test",
            "from_addr": "agent@test",
            "to_addr": "marc@test",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text
    assert PASSWORD not in response.text
    assert response.json()["smtp"]["password_set"] is True

    settings = client.get("/api/settings")
    assert settings.status_code == 200
    assert PASSWORD not in settings.text
    smtp = settings.json()["smtp"]
    assert smtp["password_set"] is True
    assert "password" not in smtp


async def test_email_failure_banner(client: TestClient) -> None:
    """Three failed sends in a row surface as a banner."""
    service = cast(Any, client.app).state.notification_service
    service._email_failures = 3
    response = client.get("/api/banners")
    assert response.status_code == 200
    banners = response.json()["items"]
    assert any(b.get("kind") == "email_failing" for b in banners)


async def test_notifications_api_lists_rows(client: TestClient) -> None:
    service = cast(Any, client.app).state.notification_service
    await service.send_test()
    response = client.get("/api/notifications")
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["event"] == "test"
    assert body["latest_id"] == body["items"][0]["id"]
