"""The notification service (docs/SPEC.md, Notifications).

One service behind every alert. A producer calls ``notify`` — engine
code directly, or the ``record_event`` sink for the durable event log —
and everything else happens in the background:

- ``notify`` never blocks: it computes the subject, channels and the
  repeat-limit/quiet-hours gates in memory and enqueues work. Nothing
  it does can delay a cycle, a position check or an order.
- A single worker writes the ``notifications`` row, publishes the
  ``notifications`` topic (in-app toast + Settings list refresh) and
  sends the email through the configured ``Mailer`` in a thread.
- Critical events always include the browser channel and are never
  throttled or held by quiet hours. Non-critical repeats inside
  ``repeat_limit_minutes`` are held back; during quiet hours
  (23:00–08:00 Amsterdam) they are held and rolled into one summary at
  the end of the window.

Email specifics: the password lives in ``secrets.smtp_password``
(encrypted, write-only); host/port/user/from/to live in
``smtp_settings`` and save only after a real test send succeeded. Three
consecutive failed sends raise a banner.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import Notification, SmtpSettings
from app.market.clock import Clock
from app.notifications.events import (
    CRITICAL,
    EVENTS,
    SYSTEM_LABELS,
    SYSTEM_SEVERITY,
    format_recorded,
)
from app.notifications.mail import Mailer, SmtpConfig, SmtpMessage, make_mailer
from app.secrets_service import SecretsStore, scrub

logger = logging.getLogger(__name__)

SUBJECT_PREFIX = "[Stock Trading Agent · PAPER]"

CHANNELS = ("app", "browser", "email")

STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_HELD_BACK = "held back"
STATUS_QUIET = "quiet hours"
STATUS_QUEUED = "queued"

EMAIL_FAILURE_BANNER_AT = 3

# Deliverable (non-app) channels, used by the quiet-hours summary union
# and the test notification.
_EXTERNAL = ("browser", "email")


class Notifier(Protocol):
    """The interface engine services depend on. ``NullNotifier`` when
    no service is wired (unit tests, standalone runs)."""

    def notify(
        self,
        event: str,
        line: str,
        *,
        details: dict[str, object] | None = None,
        body: str | None = None,
        link: str | None = None,
        severity: str | None = None,
        channels: set[str] | None = None,
        force: bool = False,
    ) -> None: ...


class NullNotifier:
    def notify(self, *_args: object, **_kwargs: object) -> None:
        return None


@dataclass
class _Pending:
    """One queued notification, fully computed by ``notify``."""

    time: datetime
    event: str
    severity: str
    subject: str
    body: str | None
    link: str | None
    channels: list[str]
    channel_status: dict[str, str]
    held_for_quiet: bool = False
    # Held rows this item summarizes (the 08:00 quiet-hours email).
    summarizes: list[int] = field(default_factory=list)


class NotificationService:
    def __init__(
        self,
        *,
        config: AppConfig,
        clock: Clock,
        secrets: SecretsStore | None = None,
        mailer: Mailer | None = None,
        publish: Callable[[set[str]], None] | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self._secrets = secrets
        self._mailer = mailer or make_mailer(config)
        self._publish = publish or (lambda topics: None)
        self._session_factory = session_factory or get_session_maker()
        self._pending: deque[_Pending] = deque()
        self._held: deque[int] = deque()  # notification ids held by quiet hours
        self._last_sent: dict[str, datetime] = {}  # subject → last send
        self._email_failures = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._lock = threading.Lock()
        self._hydrate()

    # ------------------------------------------------------------------
    # producing (sync, non-blocking)

    def notify(
        self,
        event: str,
        line: str,
        *,
        details: dict[str, object] | None = None,
        body: str | None = None,
        link: str | None = None,
        severity: str | None = None,
        channels: set[str] | None = None,
        force: bool = False,
    ) -> None:
        """Queue one notification. Never blocks and never raises — a
        notification must not be able to break a cycle, check or order."""
        try:
            self._enqueue(
                self._build(
                    event,
                    line,
                    details=details,
                    body=body,
                    link=link,
                    severity=severity,
                    channels=channels,
                    force=force,
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("notification %s failed to queue", event)

    def sink(
        self, event_type: str, level: str, details: dict[str, object] | None
    ) -> None:
        """The ``record_event`` hook: map a durable event to a notification."""
        try:
            mapped = format_recorded(event_type, details)
            if mapped is None:
                return
            event, line, link = mapped
            self.notify(event, line, link=link, details=details)
        except Exception:  # noqa: BLE001
            logger.exception("notification sink failed for %s", event_type)

    # ------------------------------------------------------------------
    # rules

    def _severity(self, event: str, severity: str | None) -> str:
        if severity is not None:
            return severity
        if event in EVENTS:
            return EVENTS[event].severity
        return SYSTEM_SEVERITY.get(event, "info")

    def _label(self, event: str) -> str:
        if event in EVENTS:
            return EVENTS[event].label
        return SYSTEM_LABELS.get(event, event.replace("_", " "))

    def _channels(self, event: str, severity: str) -> set[str]:
        configured = self.config.get(
            "notifications", "events", event, default=None
        )
        channels = {"app"}
        if isinstance(configured, list):
            channels |= {c for c in configured if c in _EXTERNAL}
        if severity == CRITICAL:
            channels.add("browser")  # criticals always reach the browser
        return channels

    @staticmethod
    def _hhmm(raw: str) -> time:
        parts = [int(p) for p in raw.split(":")[:2]]
        return time(parts[0], parts[1] if len(parts) > 1 else 0)

    def _quiet_window(self) -> tuple[time, time, ZoneInfo]:
        raw = self.config.get("notifications", "quiet_hours", default=None)
        start_raw = "23:00"
        end_raw = "08:00"
        if isinstance(raw, dict):
            start_raw = str(raw.get("start", start_raw))
            end_raw = str(raw.get("end", end_raw))
        start = self._hhmm(start_raw)
        end = self._hhmm(end_raw)
        tz = ZoneInfo(
            str(self.config.get("app", "timezone", default="Europe/Amsterdam"))
        )
        return start, end, tz

    def _in_quiet_hours(self, now: datetime) -> bool:
        start, end, tz = self._quiet_window()
        local = now.astimezone(tz).time()
        if start <= end:
            return start <= local < end
        return local >= start or local < end

    def _repeat_limit(self) -> timedelta:
        minutes = int(
            self.config.get(
                "notifications", "repeat_limit_minutes", default=30
            )
        )
        return timedelta(minutes=max(minutes, 0))

    def _build(
        self,
        event: str,
        line: str,
        *,
        details: dict[str, object] | None,
        body: str | None,
        link: str | None,
        severity: str | None,
        channels: set[str] | None,
        force: bool,
    ) -> _Pending:
        now = self.clock.now()
        severity = self._severity(event, severity)
        critical = severity == CRITICAL
        channels = (
            channels if channels is not None else self._channels(event, severity)
        ) & set(CHANNELS)
        subject = f"{SUBJECT_PREFIX} {self._label(event)}: {line}"
        status: dict[str, str] = {}
        held_for_quiet = False
        if not critical and not force:
            if self._in_quiet_hours(now):
                held_for_quiet = True
                status = {
                    c: STATUS_SENT if c == "app" else STATUS_QUIET
                    for c in channels
                }
            elif (
                self._repeat_limit() > timedelta(0)
                and (last := self._last_sent.get(subject)) is not None
                and now - last < self._repeat_limit()
            ):
                status = {
                    c: STATUS_SENT if c == "app" else STATUS_HELD_BACK
                    for c in channels
                }
        if not status:
            status = {
                c: STATUS_QUEUED if c == "email" else STATUS_SENT
                for c in channels
            }
            self._last_sent[subject] = now
        return _Pending(
            time=now,
            event=event,
            severity=severity,
            subject=subject,
            body=self._render_body(now, line, details, body, link),
            link=link,
            channels=sorted(channels),
            channel_status=status,
            held_for_quiet=held_for_quiet,
        )

    def _render_body(
        self,
        now: datetime,
        line: str,
        details: dict[str, object] | None,
        body: str | None,
        link: str | None,
    ) -> str:
        parts = [line]
        if body:
            parts += ["", body]
        if details:
            extras = "; ".join(
                f"{k}: {v}"
                for k, v in details.items()
                if v not in (None, "")
            )
            if extras:
                parts += ["", extras]
        ams = now.astimezone(self._quiet_window()[2])
        ny = now.astimezone(ZoneInfo("America/New_York"))
        parts += [
            "",
            f"Time: {ams:%H:%M} Amsterdam · {ny:%H:%M} New York",
        ]
        if link:
            parts.append(
                f"Open: http://127.0.0.1:{self.config.env.sa_port}{link}"
            )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # queue + worker

    def _enqueue(self, item: _Pending) -> None:
        with self._lock:
            self._pending.append(item)
        loop = self._loop
        if loop is not None and self._wake is not None:
            loop.call_soon_threadsafe(self._wake.set)

    def start(self) -> None:
        """Bind to the running loop and start the background worker.
        Called once from the app lifespan; tests drive ``drain`` instead."""
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._wake.set()  # flush anything queued before start()
        from app.spawn import spawn

        self._worker = spawn(self._run_worker(), "notifications-worker")

    async def _run_worker(self) -> None:
        assert self._wake is not None
        while True:
            await self._wake.wait()
            self._wake.clear()
            await self.drain()

    async def drain(self) -> None:
        """Process every queued item now (tests, send-test, shutdown)."""
        while True:
            with self._lock:
                item = self._pending.popleft() if self._pending else None
            if item is None:
                return
            await self._process(item)

    async def stop(self) -> None:
        await self.drain()
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None

    async def _process(self, item: _Pending) -> None:
        row = Notification(
            time=_naive_utc(item.time),
            event=item.event,
            severity=item.severity,
            subject=item.subject,
            body=item.body,
            link=item.link,
            channels=item.channels,
            channel_status=dict(item.channel_status),
            held_for_quiet=item.held_for_quiet,
        )
        try:
            with self._session_factory() as session:
                session.add(row)
                session.commit()
                row_id = row.id
                if item.held_for_quiet:
                    self._held.append(row_id)
                if item.summarizes:
                    held_rows = session.execute(
                        select(Notification).where(
                            Notification.id.in_(item.summarizes)
                        )
                    ).scalars()
                    for held in held_rows:
                        held.held_for_quiet = False
                    session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("could not log notification %s", item.event)
            return
        self._publish({"notifications"})
        if item.channel_status.get("email") == STATUS_QUEUED:
            await self._send_email(row_id, item.subject, item.body or "")

    # ------------------------------------------------------------------
    # quiet hours summary

    def tick(self) -> None:
        """Scheduler heartbeat: flush held notifications when quiet hours
        have ended. Also runs at startup so holds survive a restart."""
        try:
            if not self._held:
                return
            if self._in_quiet_hours(self.clock.now()):
                return
            held_ids = list(self._held)
            self._held.clear()
            subjects = self._held_subjects(held_ids)
            count = len(subjects)
            start, end, _tz = self._quiet_window()
            window = f"{start:%H:%M}–{end:%H:%M}"
            body = "\n".join(f"• {s}" for s in subjects)
            channels = {"app"} | set(self._summary_channels(held_ids))
            self._enqueue(
                _Pending(
                    time=self.clock.now(),
                    event="quiet_summary",
                    severity="info",
                    subject=(
                        f"{SUBJECT_PREFIX} Quiet hours summary: "
                        f"{count} held back"
                    ),
                    body=(
                        f"Held between quiet hours {window}:\n{body}"
                        if count
                        else "Nothing was held."
                    ),
                    link="/settings",
                    channels=sorted(channels & set(CHANNELS)),
                    channel_status={
                        c: STATUS_QUEUED if c == "email" else STATUS_SENT
                        for c in sorted(channels & set(CHANNELS))
                    },
                    summarizes=held_ids,
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("quiet hours tick failed")

    def _held_subjects(self, ids: list[int]) -> list[str]:
        with self._session_factory() as session:
            rows = session.execute(
                select(Notification.subject)
                .where(Notification.id.in_(ids))
                .order_by(Notification.time)
            ).scalars()
            return list(rows)

    def _summary_channels(self, ids: list[int]) -> set[str]:
        with self._session_factory() as session:
            rows = session.execute(
                select(Notification.channel_status).where(
                    Notification.id.in_(ids)
                )
            ).scalars()
            channels: set[str] = set()
            for status in rows:
                channels |= {
                    c for c, s in status.items() if s == STATUS_QUIET
                }
            return channels

    # ------------------------------------------------------------------
    # email

    def _smtp_fields(self) -> dict[str, object]:
        """Effective SMTP fields: the saved row first, config.yaml second."""
        with self._session_factory() as session:
            row = session.execute(
                select(SmtpSettings).order_by(SmtpSettings.id).limit(1)
            ).scalar_one_or_none()
        if row is not None and (row.host or row.to_addr):
            return {
                "host": row.host,
                "port": row.port,
                "username": row.username,
                "from_addr": row.from_addr,
                "to_addr": row.to_addr,
                "updated_at": row.updated_at,
            }
        cfg = self.config.get("notifications", "email", default=None) or {}
        return {
            "host": str(cfg.get("smtp_host", "") or ""),
            "port": int(cfg.get("smtp_port", 587) or 587),
            "username": str(cfg.get("username", "") or ""),
            "from_addr": str(cfg.get("from", "") or ""),
            "to_addr": str(cfg.get("to", "") or ""),
            "updated_at": None,
        }

    def _smtp_config(self) -> SmtpConfig | None:
        fields = self._smtp_fields()
        if not fields["host"] or not fields["to_addr"]:
            return None
        password = (
            self._secrets.get("smtp_password") if self._secrets else None
        )
        return SmtpConfig(
            host=str(fields["host"]),
            port=int(str(fields["port"] or 587)),
            username=str(fields["username"]),
            password=password,
            from_addr=str(fields["from_addr"] or fields["to_addr"]),
            to_addr=str(fields["to_addr"]),
        )

    async def _send_email(
        self, notification_id: int, subject: str, body: str
    ) -> None:
        config = self._smtp_config()
        error: str | None = None
        if config is None:
            error = "email is not configured — set it under Settings → Notifications"
        else:
            try:
                await asyncio.to_thread(
                    self._mailer.send,
                    config,
                    SmtpMessage(
                        to_addr=config.to_addr, subject=subject, body=body
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                error = scrub(
                    str(exc), self._scrub_values()
                )
        status = STATUS_FAILED if error else STATUS_SENT
        try:
            with self._session_factory() as session:
                row = session.get(Notification, notification_id)
                if row is not None:
                    row.channel_status = {
                        **row.channel_status,
                        "email": status,
                    }
                    row.error = error
                    session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("could not update notification %s", notification_id)
        from app.logging_config import ev

        if error and error.startswith("email is not configured"):
            # missing config is not a send failure — the channel status
            # already shows it on the row.
            ev(
                logger, logging.DEBUG, "notify.email_not_configured",
                "email skipped — SMTP not configured",
                notification=notification_id,
            )
        elif error:
            ev(
                logger, logging.ERROR, "notify.email_failed",
                f"notification {notification_id} email failed: {error}",
                notification=notification_id,
            )
            self._email_failures += 1
            if self._email_failures == EMAIL_FAILURE_BANNER_AT:
                self._publish({"banners"})
            logger.warning(
                "notification email failed (%s in a row): %s",
                self._email_failures,
                error,
            )
        else:
            ev(
                logger, logging.DEBUG, "notify.sent",
                f"notification {notification_id} emailed",
                notification=notification_id,
            )
            self._email_failures = 0
        self._publish({"notifications"})

    def _scrub_values(self) -> list[str]:
        if self._secrets is None:
            return []
        return self._secrets.scrub_values()

    @property
    def email_failures(self) -> int:
        return self._email_failures

    # ------------------------------------------------------------------
    # SMTP settings (saved on their own, after a successful test send)

    async def save_smtp(
        self,
        *,
        host: str,
        port: int,
        username: str,
        from_addr: str,
        to_addr: str,
        password: str | None = None,
    ) -> dict[str, object]:
        """Test the details with a real send, then save. Never saves on
        a failed test; the password goes to ``secrets.smtp_password``."""
        if password is None and self._secrets is not None:
            password = self._secrets.get("smtp_password")
        config = SmtpConfig(
            host=host,
            port=port,
            username=username,
            password=password,
            from_addr=from_addr,
            to_addr=to_addr,
        )
        message = SmtpMessage(
            to_addr=to_addr,
            subject=f"{SUBJECT_PREFIX} Test: SMTP settings saved",
            body=(
                "These SMTP details were just saved in Stock Trading Agent.\n\n"
                f"Time: {self.clock.now():%Y-%m-%d %H:%M} UTC"
            ),
        )
        try:
            await asyncio.to_thread(self._mailer.send, config, message)
        except Exception as exc:  # noqa: BLE001
            detail = scrub(str(exc), self._scrub_values() + [password or ""])
            self._record_smtp_test(False, detail)
            return {"ok": False, "error": detail}
        now = _naive_utc(self.clock.now())
        with self._session_factory() as session:
            row = session.execute(
                select(SmtpSettings).order_by(SmtpSettings.id).limit(1)
            ).scalar_one_or_none()
            if row is None:
                row = SmtpSettings(
                    host=host,
                    port=port,
                    username=username,
                    from_addr=from_addr,
                    to_addr=to_addr,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.host = host
                row.port = port
                row.username = username
                row.from_addr = from_addr
                row.to_addr = to_addr
                row.updated_at = now
            session.commit()
        if password is not None and self._secrets is not None:
            self._secrets.save(
                "smtp_password", password, "ok", "test send ok"
            )
        self._record_smtp_test(True, "test send ok")
        self._publish({"settings", "notifications"})
        return {"ok": True}

    def _record_smtp_test(self, ok: bool, detail: str) -> None:
        """The 'test sent' chip on the SMTP card lives on the
        ``smtp_password`` secret row's test fields."""
        try:
            if self._secrets is not None and self._secrets.get("smtp_password"):
                self._secrets.record_test("smtp_password", ok, detail)
        except Exception:  # noqa: BLE001
            logger.exception("could not record smtp test")

    def smtp_status(self) -> dict[str, object]:
        """Everything the SMTP card may show — never the password."""
        fields = self._smtp_fields()
        password_set = bool(
            self._secrets and self._secrets.get("smtp_password")
        )
        tested_at = test_result = test_detail = None
        if self._secrets is not None:
            meta = self._secrets.secret_status("smtp_password")
            if meta is not None:
                tested_at = meta.get("tested_at")
                test_result = meta.get("test_result")
                test_detail = meta.get("test_detail")
        updated = fields.get("updated_at")
        return {
            "configured": bool(fields["host"] and fields["to_addr"]),
            "host": fields["host"],
            "port": fields["port"],
            "username": fields["username"],
            "from_addr": fields["from_addr"],
            "to_addr": fields["to_addr"],
            "password_set": password_set,
            "saved_at": (
                updated.isoformat() + "Z"
                if isinstance(updated, datetime)
                else None
            ),
            "tested_at": tested_at,
            "test_result": test_result,
            "test_detail": test_detail,
        }

    async def send_test(self) -> dict[str, object]:
        """The 'Send test' button: one notification on every channel."""
        item = self._build(
            "test",
            "this is a test notification",
            details=None,
            body=None,
            link="/settings",
            severity="info",
            channels={"app", "browser", "email"},
            force=True,
        )
        await self._process(item)
        return {"ok": True}

    # ------------------------------------------------------------------
    # API reads

    def recent(
        self, *, after_id: int = 0, limit: int = 20
    ) -> dict[str, object]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(Notification)
                    .where(Notification.id > after_id)
                    .order_by(desc(Notification.id))
                    .limit(min(limit, 100))
                )
                .scalars()
                .all()
            )
        items = [
            {
                "id": row.id,
                "time": row.time.isoformat() + "Z",
                "event": row.event,
                "severity": row.severity,
                "subject": row.subject,
                "body": row.body,
                "link": row.link,
                "channels": row.channels,
                "channel_status": row.channel_status,
                "error": row.error,
            }
            for row in rows
        ]
        return {
            "items": items,
            "latest_id": items[0]["id"] if items else after_id,
        }

    # ------------------------------------------------------------------
    # restart hydration

    def _hydrate(self) -> None:
        """Rebuild throttle/held/failure state from the log so a restart
        doesn't re-send bursts or lose a quiet-hours hold."""
        try:
            with self._session_factory() as session:
                held = session.execute(
                    select(Notification.id).where(
                        Notification.held_for_quiet.is_(True)
                    )
                ).scalars()
                self._held = deque(held)
                rows = session.execute(
                    select(Notification)
                    .order_by(desc(Notification.id))
                    .limit(500)
                ).scalars()
                seen: set[str] = set()
                for row in rows:
                    if row.subject in seen:
                        continue
                    if all(
                        s in (STATUS_HELD_BACK, STATUS_QUIET)
                        for c, s in row.channel_status.items()
                        if c != "app"
                    ) and any(
                        s in (STATUS_HELD_BACK, STATUS_QUIET)
                        for s in row.channel_status.values()
                    ):
                        continue  # never actually sent — no throttle credit
                    seen.add(row.subject)
                    self._last_sent[row.subject] = row.time.replace(
                        tzinfo=ZoneInfo("UTC")
                    )
                failures = 0
                for row in rows:
                    status = row.channel_status.get("email")
                    if status == STATUS_FAILED:
                        failures += 1
                    elif status == STATUS_SENT:
                        break
                self._email_failures = failures
        except Exception:  # noqa: BLE001
            logger.exception("could not hydrate notification state")
