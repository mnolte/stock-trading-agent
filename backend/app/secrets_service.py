"""Encrypted API keys (docs/SPEC.md, API keys).

The keys — ``alpaca_key_id``, ``alpaca_secret``, ``openai``,
``anthropic``, ``finnhub`` and the SMTP ``smtp_password`` — live in the ``secrets``
table, Fernet-encrypted. The key
file is ``SA_DATA_DIR/secret.key`` (created at first start, mode 600,
never in the DB). Reads never expose values: only the name, the last
four characters, timestamps and the test result cross the API.

A stored key is tested with one real request per service (Alpaca paper
account, OpenAI models list, one Finnhub earnings request). A failed
candidate test never replaces the old key. Keys found in .env or the
environment are imported once, then ignored with a warning and a banner.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import ROOT, AppConfig
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import Secret
from app.engine.state import record_event
from app.market.clock import Clock

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

SECRET_NAMES = (
    "alpaca_key_id",
    "alpaca_secret",
    "openai",
    "anthropic",
    "finnhub",
    "smtp_password",
)

# The UI treats the Alpaca pair as one entry: three "keys" in the GUI.
GROUPS: dict[str, tuple[str, ...]] = {
    "alpaca": ("alpaca_key_id", "alpaca_secret"),
    "openai": ("openai",),
    "anthropic": ("anthropic",),
    "finnhub": ("finnhub",),
}
GROUP_LABELS = {
    "alpaca": "Alpaca keys",
    "openai": "OpenAI key",
    "anthropic": "Anthropic key",
    "finnhub": "Finnhub key",
}
GROUP_ORDER = ("alpaca", "openai", "anthropic", "finnhub")

ENV_VARS = {
    "alpaca_key_id": "ALPACA_API_KEY",
    "alpaca_secret": "ALPACA_SECRET_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "finnhub": "FINNHUB_API_KEY",
}

# Clients are always constructed, even without a key, so a later save can
# swap credentials in place; the placeholder fails auth until then.
MISSING_KEY = "missing"


def masked_hint(name: str, value: str) -> str:
    """The only key text that ever leaves the backend: a masked hint."""
    last4 = value[-4:] if len(value) >= 4 else value
    if name == "alpaca_key_id" and value.startswith("PK"):
        return f"PK••••{last4}"
    if name == "openai" and value.startswith("sk-"):
        return f"sk-••••{last4}"
    if name == "anthropic" and value.startswith("sk-ant-"):
        return f"sk-ant-••••{last4}"
    return f"••••{last4}"


def masked_account(number: str) -> str:
    """Account number for the GUI: enough to recognise it, nothing more."""
    if len(number) >= 7:
        return f"{number[:4]}…{number[-3:]}"
    return number


def scrub(text: str, secrets: list[str]) -> str:
    """Strip every known secret value out of a message."""
    for value in secrets:
        if value and len(value) >= 8:
            text = text.replace(value, "••••")
    return text


def read_env_keys() -> dict[str, str]:
    """Key values still present in .env or the real environment.

    ``SA_ENV_FILE`` overrides the .env path (tests point it at an empty
    file so nothing real leaks into a test database).
    """
    path = Path(os.environ.get("SA_ENV_FILE") or (ROOT / ".env"))
    file_values = dotenv_values(str(path)) if path.is_file() else {}
    merged: dict[str, str] = {
        key: value for key, value in file_values.items() if value
    }
    merged.update(
        {key: value for key, value in os.environ.items() if value}
    )
    return {
        name: merged[var]
        for name, var in ENV_VARS.items()
        if merged.get(var)
    }


def _load_or_create_key(path: Path) -> Fernet:
    """Read the Fernet key, creating it mode-600 at first start."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Heal wrong permissions: the file must never be group/world readable.
        os.chmod(path, 0o600)
        return Fernet(path.read_bytes().strip())
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return Fernet(key)


@dataclass(frozen=True)
class KeyTestResult:
    """Outcome of one service test. ``detail`` is non-secret context
    (masked account number); ``error`` is already scrubbed."""

    ok: bool
    detail: str | None = None
    error: str | None = None
    account: str | None = None  # full account number, for change detection


class KeyTesters(Protocol):
    """One real request per service. Faked in simulated mode and tests."""

    async def alpaca(self, key_id: str, secret: str) -> KeyTestResult: ...

    async def openai(self, key: str) -> KeyTestResult: ...

    async def anthropic(self, key: str) -> KeyTestResult: ...

    async def finnhub(self, key: str) -> KeyTestResult: ...


class LiveKeyTesters:
    """The spec's tests: Alpaca GET account on the paper endpoint (must be
    paper), OpenAI list models, Finnhub one earnings request."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def alpaca(self, key_id: str, secret: str) -> KeyTestResult:
        from app.data.alpaca import AlpacaDataClient

        try:
            client = AlpacaDataClient(key_id, secret, clock=self._clock)
        except Exception as exc:  # noqa: BLE001
            return KeyTestResult(
                ok=False, error=scrub(str(exc), [key_id, secret])
            )
        result = await client.check_paper_account()
        if result.error is not None:
            return KeyTestResult(
                ok=False, error=scrub(result.error, [key_id, secret])
            )
        number = (
            str(result.data.get("account_number", ""))
            if isinstance(result.data, dict)
            else ""
        )
        return KeyTestResult(
            ok=True,
            detail=f"Account {masked_account(number)}" if number else None,
            account=number or None,
        )

    async def openai(self, key: str) -> KeyTestResult:
        from app.llm.providers.openai import test_key

        ok, error = await test_key(key)
        return KeyTestResult(ok=ok, error=scrub(error or "", [key]) or None)

    async def anthropic(self, key: str) -> KeyTestResult:
        from app.llm.providers.anthropic import test_key

        ok, error = await test_key(key)
        return KeyTestResult(ok=ok, error=scrub(error or "", [key]) or None)

    async def finnhub(self, key: str) -> KeyTestResult:
        import asyncio

        import finnhub

        try:
            client = finnhub.Client(api_key=key)
            start = self._clock.now().date()
            await asyncio.to_thread(
                client.earnings_calendar,
                start.isoformat(),
                (start + timedelta(days=30)).isoformat(),
                "SPY",
            )
            return KeyTestResult(ok=True)
        except Exception as exc:  # noqa: BLE001
            return KeyTestResult(ok=False, error=scrub(str(exc), [key]))


class FakeKeyTesters:
    """Offline testers for simulated mode and tests.

    ``results`` overrides the default pass; ``calls`` records every test
    so a test can assert which key was checked.
    """

    def __init__(self, results: Mapping[str, KeyTestResult] | None = None) -> None:
        self.results = dict(results or {})
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def alpaca(self, key_id: str, secret: str) -> KeyTestResult:
        self.calls.append(("alpaca", (key_id, secret)))
        return self.results.get("alpaca") or KeyTestResult(
            ok=True, detail="Account PA3K…001", account="PA3K0000001"
        )

    async def openai(self, key: str) -> KeyTestResult:
        self.calls.append(("openai", (key,)))
        return self.results.get("openai") or KeyTestResult(ok=True)

    async def anthropic(self, key: str) -> KeyTestResult:
        self.calls.append(("anthropic", (key,)))
        return self.results.get("anthropic") or KeyTestResult(ok=True)

    async def finnhub(self, key: str) -> KeyTestResult:
        self.calls.append(("finnhub", (key,)))
        return self.results.get("finnhub") or KeyTestResult(ok=True)


def make_key_testers(config: AppConfig, clock: Clock) -> KeyTesters:
    if config.env.mode == "simulated":
        return FakeKeyTesters()
    return LiveKeyTesters(clock)


class _ScrubSecretsFilter(logging.Filter):
    """Compatibility shim kept for tests: registers the provider so the
    shared pipeline filter (``app.logging_config.SecretScrubFilter``)
    scrubs stored keys on every handler."""

    def __init__(self, secrets: Callable[[], list[str]]) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        values = [v for v in self._secrets() if v and len(v) >= 8]
        if not values:
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        if not any(v in message for v in values):
            return True
        record.msg = scrub(message, values)
        record.args = ()
        if record.exc_text:
            record.exc_text = scrub(record.exc_text, values)
        return True


def install_secret_scrub_filter(secrets: Callable[[], list[str]]) -> None:
    """Feed the live key values to the shared scrub filter and attach a
    local scrub filter to any handler that predates the pipeline (keeps
    the unit test's own handler covered)."""
    from app.logging_config import SecretScrubFilter, register_secret_provider

    register_secret_provider(secrets)
    shared = SecretScrubFilter()
    loggers = [logging.getLogger()]
    loggers += [
        logging.getLogger(name)
        for name in logging.root.manager.loggerDict
        if isinstance(logging.root.manager.loggerDict[name], logging.Logger)
    ]
    for log in loggers:
        for handler in log.handlers:
            handler.addFilter(shared)


class SecretsStore:
    """Encrypted, write-only key storage wired to the runtime services."""

    def __init__(
        self,
        config: AppConfig,
        clock: Clock,
        session_factory: sessionmaker[Session] | None = None,
        key_path: Path | None = None,
    ) -> None:
        self._clock = clock
        self._factory = session_factory or get_session_maker()
        path = key_path or Path(config.env.sa_data_dir) / "secret.key"
        self._fernet = _load_or_create_key(path)
        self._values: dict[str, str] = {}
        self._meta: dict[str, Secret] = {}
        self._load_rows()
        # Filled by import_env_keys: what .env still contains and what this
        # startup imported. Drives the "remove them from .env" banner.
        self.env_present: list[str] = []
        self.imported: list[str] = []
        self._env_leftovers: list[str] = []

    # ------------------------------------------------------------------
    # storage

    def _load_rows(self) -> None:
        with self._factory() as session:
            rows = session.execute(select(Secret)).scalars().all()
            self._meta = {row.name: row for row in rows}
            self._values = {
                row.name: self._fernet.decrypt(
                    row.value_encrypted.encode()
                ).decode()
                for row in rows
            }

    def get(self, name: str) -> str | None:
        """Plaintext for backend wiring only; never crosses the API."""
        return self._values.get(name)

    def alpaca_pair(self) -> tuple[str | None, str | None]:
        return self.get("alpaca_key_id"), self.get("alpaca_secret")

    def scrub_values(self) -> list[str]:
        """Everything the log scrubber must strip: stored keys plus any
        plaintext still seen in .env this boot."""
        return [*self._values.values(), *self._env_leftovers]

    def save(
        self,
        name: str,
        value: str,
        test_result: str | None = None,
        test_detail: str | None = None,
    ) -> None:
        """Encrypt and store one key; refresh its test state when given."""
        now = _naive_utc(self._clock.now())
        encrypted = self._fernet.encrypt(value.encode()).decode()
        with self._factory() as session:
            row = session.execute(
                select(Secret).where(Secret.name == name)
            ).scalar_one_or_none()
            if row is None:
                row = Secret(name=name, value_encrypted=encrypted, last4="")
                session.add(row)
            row.value_encrypted = encrypted
            row.last4 = value[-4:]
            row.saved_at = now
            if test_result is not None:
                row.test_result = test_result
                row.test_detail = scrub(test_detail or "", [value]) or None
                row.tested_at = now
            session.commit()
        self._values[name] = value
        self._load_rows()

    def record_test(
        self, name: str, ok: bool, detail: str | None = None
    ) -> None:
        """Update the stored key's test state without touching the value."""
        now = _naive_utc(self._clock.now())
        with self._factory() as session:
            row = session.execute(
                select(Secret).where(Secret.name == name)
            ).scalar_one_or_none()
            if row is not None:
                row.tested_at = now
                row.test_result = "ok" if ok else "failed"
                row.test_detail = (
                    scrub(detail, [self._values.get(name, "")]) if detail else None
                )
                session.commit()
        self._load_rows()

    def record_event(
        self,
        name: str,
        action: str,
        ok: bool,
        detail: str | None = None,
        last4: str | None = None,
    ) -> None:
        """Audit row: name, last 4 and result only — never the key."""
        row = self._meta.get(name)
        tail = last4 or (row.last4 if row is not None else None)
        with self._factory() as session:
            record_event(
                session,
                self._clock,
                level="info" if ok else "warning",
                event_type="api_key",
                details={
                    "name": name,
                    "last4": tail,
                    "action": action,
                    "result": "ok" if ok else "failed",
                    "detail": scrub(detail, self.scrub_values()) if detail else None,
                },
            )
            session.commit()

    # ------------------------------------------------------------------
    # status + gates

    def group_status(self, group: str) -> dict[str, object]:
        """One API/UI row for a key group — no secret ever leaves here."""
        names = GROUPS[group]
        rows = [self._meta.get(name) for name in names]
        stored = all(row is not None for row in rows)
        if not stored:
            return {
                "name": group,
                "set": False,
                "status": "missing",
                "masked": None,
                "account": None,
                "saved_at": None,
                "tested_at": None,
                "error": None,
            }
        results = {row.test_result for row in rows if row is not None}
        status = (
            "ok"
            if results == {"ok"}
            else "failing"
            if "failed" in results
            else "untested"
        )
        # The masked hint comes from the key id (Alpaca) or the key itself.
        shown = self._values.get(names[0], "")
        detail = next(
            (row.test_detail for row in rows if row and row.test_detail), None
        )
        account = None
        if group == "alpaca" and detail and detail.startswith("Account "):
            account = detail.removeprefix("Account ")
        error = detail if status == "failing" else None
        def _iso(value: datetime | None) -> str | None:
            if value is None:
                return None
            return value.replace(tzinfo=None).isoformat() + "Z"
        saved_at = max(
            (s for row in rows if row is not None and (s := row.saved_at) is not None),
            default=None,
        )
        tested_at = max(
            (t for row in rows if row is not None and (t := row.tested_at) is not None),
            default=None,
        )
        return {
            "name": group,
            "set": True,
            "status": status,
            "masked": masked_hint(names[0], shown),
            "account": account,
            "saved_at": _iso(saved_at),
            "tested_at": _iso(tested_at),
            "error": error,
        }

    def secret_status(self, name: str) -> dict[str, object] | None:
        """Metadata for one stored secret — timestamps and test result,
        never the value. Used for the SMTP password's test chip."""
        row = self._meta.get(name)
        if row is None:
            return None

        def _iso(value: datetime | None) -> str | None:
            if value is None:
                return None
            return value.replace(tzinfo=None).isoformat() + "Z"

        return {
            "saved_at": _iso(row.saved_at),
            "tested_at": _iso(row.tested_at),
            "test_result": row.test_result,
            "test_detail": row.test_detail,
        }

    def missing_groups(self) -> list[str]:
        """Groups whose keys are missing or failing; resume stays blocked."""
        return [
            group
            for group in GROUP_ORDER
            if self.group_status(group)["status"] != "ok"
        ]

    def provider_ok(self, provider: str) -> bool:
        """True when that provider's stored key passes its test."""
        return self.group_status(provider)["status"] == "ok"

    def missing_resume_groups(self) -> list[str]:
        """Keys Resume actually needs (docs/SPEC.md → API keys):
        Alpaca and Finnhub passing, at least one model key set and
        passing, and a passing key for every provider live decisions
        and the three review tasks use."""
        from app.config import get_config
        from app.llm.models import REVIEW_TASKS, model_provider, review_model

        out = [
            group
            for group in ("alpaca", "finnhub")
            if self.group_status(group)["status"] != "ok"
        ]
        config = get_config()
        used = {model_provider(config, str(config.get("decision", "model", default="")))}
        used |= {
            model_provider(config, review_model(config, task)) for task in REVIEW_TASKS
        }
        for provider in sorted(used):
            if not self.provider_ok(provider):
                out.append(provider)
        return out

    def all_ready(self) -> bool:
        return not self.missing_resume_groups()

    def remove(self, name: str) -> None:
        """Delete a stored key (behind "Confirm it is you" at the API)."""
        from sqlalchemy import delete

        with self._factory() as session:
            session.execute(delete(Secret).where(Secret.name == name))
            session.commit()
        self._values.pop(name, None)
        self._load_rows()

    # ------------------------------------------------------------------
    # tests + import

    async def retest_stored(
        self, group: str, testers: KeyTesters, *, event: bool = True
    ) -> KeyTestResult | None:
        """Re-run the service test on the stored key; updates its result."""
        if group == "alpaca":
            key_id, secret = self.alpaca_pair()
            if not key_id or not secret:
                return None
            result = await testers.alpaca(key_id, secret)
            for name in GROUPS["alpaca"]:
                self.record_test(name, result.ok, result.detail or result.error)
        else:
            value = self.get(group)
            if not value:
                return None
            result = await (
                testers.openai(value)
                if group == "openai"
                else testers.anthropic(value)
                if group == "anthropic"
                else testers.finnhub(value)
            )
            self.record_test(group, result.ok, result.detail or result.error)
        if event:
            self.record_event(
                group, "tested", result.ok, result.detail or result.error
            )
        return result

    async def import_env_keys(
        self, env: Mapping[str, str], testers: KeyTesters
    ) -> None:
        """One-time import of keys still found in .env/the environment.

        A key with nothing stored under its name is tested and stored
        (with its test result, pass or fail); a key with a stored value
        is ignored. Afterwards env keys are ignored entirely — the
        caller keeps them out of every client — and the warning plus the
        banner ask the user to remove them.
        """
        found = {name: value for name, value in env.items() if value}
        self.env_present = sorted(found)
        self._env_leftovers = list(found.values())
        if not found:
            return

        ignored: list[str] = []
        key_id, secret = found.get("alpaca_key_id"), found.get("alpaca_secret")
        if key_id or secret:
            if self.get("alpaca_key_id") or self.get("alpaca_secret"):
                ignored += [
                    n for n in GROUPS["alpaca"] if found.get(n)
                ]
            else:
                if key_id and secret:
                    result = await testers.alpaca(key_id, secret)
                    detail = result.detail or result.error
                    outcome = "ok" if result.ok else "failed"
                else:
                    detail = "incomplete pair in .env"
                    outcome = "failed"
                for name in GROUPS["alpaca"]:
                    value = found.get(name)
                    if value:
                        self.save(name, value, outcome, detail)
                        self.record_event(name, "imported", outcome == "ok", detail)
                        self.imported.append(name)

        for name in ("openai", "anthropic", "finnhub"):
            value = found.get(name)
            if not value:
                continue
            if self.get(name):
                ignored.append(name)
                continue
            tester = (
                testers.openai
                if name == "openai"
                else testers.anthropic
                if name == "anthropic"
                else testers.finnhub
            )
            result = await tester(value)
            self.save(
                name,
                value,
                "ok" if result.ok else "failed",
                result.detail or result.error,
            )
            self.record_event(name, "imported", result.ok, result.detail or result.error)
            self.imported.append(name)

        if self.imported:
            logger.warning(
                "API keys were imported from the environment and stored "
                "encrypted. Environment keys are ignored from now on — "
                "remove them from .env."
            )
        if ignored:
            logger.warning(
                "API keys found in the environment are ignored; keys live "
                "in Settings. Remove them from .env."
            )
