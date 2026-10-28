"""PR 28 step 7 — stored credentials go only where they were saved."""

from __future__ import annotations

import smtplib
import threading

import pytest
from app.secrets_service import KeyTestResult
from fastapi.testclient import TestClient

SMTP_BODY = {
    "host": "mail.saved.example",
    "port": 587,
    "username": "user@saved.example",
    "from_addr": "a@b.c",
    "to_addr": "d@e.f",
    "password": "first-password",
}


class _FakeSmtp:
    """Stands in for smtplib.SMTP — no network, records everything."""

    calls: list[tuple] = []

    def __init__(self, host: str, port: int, timeout: float | None = None):
        _FakeSmtp.calls.append(("connect", host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        _FakeSmtp.calls.append(("ehlo",))

    def starttls(self):
        _FakeSmtp.calls.append(("starttls",))

    def login(self, user: str, password: str):
        _FakeSmtp.calls.append(("login", user, password))

    def send_message(self, msg):
        _FakeSmtp.calls.append(("send",))


class _RecMailer:
    """Records the SmtpConfig the service hands the mailer — password and
    destination included."""

    def __init__(self) -> None:
        self.configs = []

    def send(self, config, message) -> None:
        self.configs.append(config)


def _smtp_service(client: TestClient):
    svc = client.app.state.notification_service
    assert svc is not None
    return svc


def _smtp_configured(client: TestClient) -> _RecMailer:
    mailer = _RecMailer()
    _smtp_service(client)._mailer = mailer
    r = client.post("/api/notifications/smtp", json=SMTP_BODY)
    assert r.status_code == 200, r.text
    return mailer


def test_changed_host_without_password_is_422_and_no_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailer = _smtp_configured(client)
    mailer.configs.clear()
    r = client.post(
        "/api/notifications/smtp",
        json={**SMTP_BODY, "host": "mail.evil.example", "password": ""},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "password_required"
    # The stored password never travels to a changed server — the mailer
    # is never invoked (the spec's "the fake server sees no connection").
    assert mailer.configs == []


def test_changed_host_with_password_logs_in_with_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailer = _smtp_configured(client)
    mailer.configs.clear()
    r = client.post(
        "/api/notifications/smtp",
        json={**SMTP_BODY, "host": "mail.new.example", "password": "new-secret"},
    )
    assert r.status_code == 200, r.text
    assert len(mailer.configs) == 1
    config = mailer.configs[0]
    assert config.host == "mail.new.example" and config.port == 587
    # The request's own password travels to the new server — the stored
    # one stays behind.
    assert config.password == "new-secret"
    assert config.username == "user@saved.example"


def test_smtp_mailer_connects_and_logs_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """SmtpMailer.send does ehlo → STARTTLS → login with the configured
    credentials on a 10 s timeout (spec: fake SMTP server sees it)."""
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    _FakeSmtp.calls.clear()
    from app.notifications.mail import SmtpConfig, SmtpMailer, SmtpMessage

    SmtpMailer().send(
        SmtpConfig(
            host="mail.example", port=587, username="u@x",
            password="p4ss", from_addr="a@b.c", to_addr="d@e.f",
        ),
        SmtpMessage(to_addr="d@e.f", subject="t", body="b"),
    )
    assert _FakeSmtp.calls == [
        ("connect", "mail.example", 587, 10),
        ("ehlo",),
        ("starttls",),
        ("ehlo",),
        ("login", "u@x", "p4ss"),
        ("send",),
    ]


def test_different_paper_account_requires_new_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keys testing to a different account save but arm the kill switch —
    Start new run is the only way out."""
    from types import SimpleNamespace

    state = client.app.state
    state.account = SimpleNamespace(account_number="PA3K0000001")
    state.key_testers.results["alpaca"] = KeyTestResult(
        ok=True, account="PA-OTHER-999", detail="Account PA9…999"
    )
    r = client.post(
        "/api/keys/alpaca", json={"key_id": "PKOTHER999", "secret": "s" * 40}
    )
    assert r.status_code == 200, r.text
    assert r.json()["account_changed"] is True
    assert client.get("/api/header").json()["kill_switch"] is True


# ---------------------------------------------------------------------
# run.lock: exactly one starter wins


def test_two_simultaneous_acquires_exactly_one_wins(tmp_path) -> None:
    from app.shutdown import RunLock

    lock_path = tmp_path / "data" / "run.lock"
    outcome: dict[str, str] = {}

    def go(name: str) -> None:
        try:
            RunLock(lock_path).acquire(heartbeat_seconds=30)
            outcome[name] = "won"
        except SystemExit:
            outcome[name] = "refused"

    threads = [threading.Thread(target=go, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcome.values()) == ["refused", "won"]


def test_lock_holder_blocks_a_second_process(tmp_path) -> None:
    """A fresh lock written by the test pid refuses the next starter."""
    from app.shutdown import RunLock

    lock_path = tmp_path / "data" / "run.lock"
    RunLock(lock_path).acquire(heartbeat_seconds=30)
    with pytest.raises(SystemExit):
        RunLock(lock_path).acquire(heartbeat_seconds=30)
