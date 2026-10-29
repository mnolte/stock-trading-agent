"""SMTP delivery for the email notification channel.

One narrow interface — ``Mailer.send`` — behind which the real client
(``smtplib`` + STARTTLS) and the test fake live. ``send`` is blocking
stdlib I/O, so callers run it through ``asyncio.to_thread``. The SMTP
password arrives as a function argument and never lands on the message
or the log.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from app.config import AppConfig

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10  # seconds; also caps the greeting/STARTTLS/login


@dataclass(frozen=True)
class SmtpConfig:
    """Everything one send needs, from ``smtp_settings`` + secrets."""

    host: str
    port: int
    username: str
    password: str | None
    from_addr: str
    to_addr: str


@dataclass(frozen=True)
class SmtpMessage:
    to_addr: str
    subject: str
    body: str


class Mailer(Protocol):
    """Blocking send; returns None or raises. Faked in tests."""

    def send(self, config: SmtpConfig, message: SmtpMessage) -> None: ...


class SmtpMailer:
    """STARTTLS first, then login when a username is configured."""

    def send(self, config: SmtpConfig, message: SmtpMessage) -> None:
        email = EmailMessage()
        email["From"] = config.from_addr
        email["To"] = message.to_addr
        email["Subject"] = message.subject
        email.set_content(message.body)
        with smtplib.SMTP(
            config.host, config.port, timeout=CONNECT_TIMEOUT
        ) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if config.username:
                server.login(config.username, config.password or "")
            server.send_message(email)


class FakeMailer:
    """Records every send; ``fail_with`` raises on demand. No sockets."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.sent: list[SmtpMessage] = []
        self.fail_with = fail_with

    def send(self, config: SmtpConfig, message: SmtpMessage) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(message)


def make_mailer(config: AppConfig) -> Mailer:
    """Simulated mode keeps mail local: records sends, never connects."""
    if config.env.mode == "simulated":
        return FakeMailer()
    return SmtpMailer()
