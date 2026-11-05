"""Smoke tests for the manual dev harnesses.

``app.broker_guard`` gets a real exercised path (fake secrets store and
fake broker). ``app.smoke_data``, ``app.smoke_execution`` and
``app.eval_ask`` are wired to the live paper account / a real model by
design — sockets are blocked here, so the tests cover the parts that
can run locally: the simulated-mode refusal, the read-only broker
wrapper, argument parsing and the per-question scoring against a fake
``answer`` stream.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import app.ask.service as ask_service
import app.broker_guard as broker_guard
import app.eval_ask as eval_ask
import app.smoke_data as smoke_data
import pytest
from app.data.models import DataResult
from app.smoke_execution import BLOCKED, ReadOnlyBroker
from sqlalchemy.orm import Session, sessionmaker

# ----------------------------------------------------------------------
# broker_guard


class _NoKeysStore:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def alpaca_pair(self) -> tuple[str | None, str | None]:
        return None, None


class _KeysStore(_NoKeysStore):
    def alpaca_pair(self) -> tuple[str | None, str | None]:
        return "fake-key", "fake-secret"


class FakeGuardBroker:
    """Stands in for AlpacaPaperBroker; records the write and returns a
    canned read-back."""

    written: dict[str, object] | None = None
    read_back: dict[str, object] = {
        "no_shorting": True,
        "max_margin_multiplier": "1",
    }

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def set_account_config(
        self, **fields: object
    ) -> DataResult[dict[str, object] | None]:
        type(self).written = fields
        return DataResult(data=dict(type(self).read_back), error=None)

    async def get_account_config(
        self,
    ) -> DataResult[dict[str, object] | None]:
        return DataResult(data=dict(type(self).read_back), error=None)


def test_broker_guard_without_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(broker_guard, "SecretsStore", _NoKeysStore)
    rc = asyncio.run(broker_guard._main())
    assert rc == 1
    assert "no Alpaca keys stored" in capsys.readouterr().out


def test_broker_guard_confirmed(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    FakeGuardBroker.written = None
    FakeGuardBroker.read_back = {
        "no_shorting": True,
        "max_margin_multiplier": "1",
    }
    monkeypatch.setattr(broker_guard, "SecretsStore", _KeysStore)
    monkeypatch.setattr(broker_guard, "AlpacaPaperBroker", FakeGuardBroker)
    rc = asyncio.run(broker_guard._main())
    out = capsys.readouterr().out
    assert rc == 0
    assert "guard confirmed" in out
    assert FakeGuardBroker.written == {
        "no_shorting": True,
        "max_margin_multiplier": "1",
    }


def test_broker_guard_not_confirmed(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    FakeGuardBroker.read_back = {
        "no_shorting": False,
        "max_margin_multiplier": "2",
    }
    monkeypatch.setattr(broker_guard, "SecretsStore", _KeysStore)
    monkeypatch.setattr(broker_guard, "AlpacaPaperBroker", FakeGuardBroker)
    rc = asyncio.run(broker_guard._main())
    assert rc == 1
    assert "GUARD NOT CONFIRMED" in capsys.readouterr().out


# ----------------------------------------------------------------------
# smoke_data — needs the real paper account; simulated mode refuses.


def test_smoke_data_refuses_simulated_mode(capsys: Any) -> None:
    rc = smoke_data.main()
    assert rc == 2
    assert "MODE=paper" in capsys.readouterr().out


def test_smoke_data_line() -> None:
    assert smoke_data._line("src", "ok") == 0
    assert smoke_data._line("src", "", "down") == 1


# ----------------------------------------------------------------------
# smoke_execution — the read-only wrapper must never let a write reach
# the broker, whatever it wraps.


class FakeInnerBroker:
    def __init__(self) -> None:
        self.write_attempts = 0

    async def get_account(self) -> DataResult[object]:
        return DataResult(data=object(), error=None)

    async def submit_market_sell(self, **_kw: Any) -> DataResult[object]:
        self.write_attempts += 1
        return DataResult(data=object(), error=None)


def test_read_only_broker_blocks_writes() -> None:
    inner = FakeInnerBroker()
    broker = ReadOnlyBroker(inner)  # type: ignore[arg-type]

    write = asyncio.run(
        broker.submit_market_sell(
            symbol="AAPL", qty=1, market_on_open=False,
            client_order_id="t",
        )
    )
    assert write.error == BLOCKED
    assert inner.write_attempts == 0

    cancel = asyncio.run(broker.cancel_order("abc"))
    assert cancel.error == BLOCKED


def test_read_only_broker_passes_reads() -> None:
    inner = FakeInnerBroker()
    broker = ReadOnlyBroker(inner)  # type: ignore[arg-type]
    read = asyncio.run(broker.get_account())
    assert read.error is None
    assert read.data is not None


# ----------------------------------------------------------------------
# eval_ask — arg parsing and the per-question scoring against a fake
# answer stream (the real one calls a model).


def test_eval_ask_requires_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # argparse exits on a missing required --model
    monkeypatch.setattr("sys.argv", ["eval_ask"])
    with pytest.raises(SystemExit):
        eval_ask.main()


def test_eval_ask_run_question(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
) -> None:
    async def fake_answer(**_kwargs: Any) -> Any:
        yield {"event": "step", "data": {"label": "positions"}}
        yield {"event": "delta", "data": {"text": "You hold 3 positions."}}
        yield {
            "event": "done",
            "data": {
                "text": "You hold 3 positions.",
                "references": ["positions"],
                "cost": str(Decimal("0.001")),
            },
        }

    monkeypatch.setattr(ask_service, "answer", fake_answer)
    result = asyncio.run(
        eval_ask._run_question(
            {
                "q": "what do I hold",
                "facts": ["3 positions"],
                "properties": [
                    "uses_tools", "cites_refs", "no_unverified", "concise",
                ],
            },
            deps=object(),
            client=object(),
            session_factory=session_factory,
        )
    )
    assert result["passed"] is True
    assert result["facts"] == [True]
    assert result["steps"] == ["positions"]


def test_eval_ask_run_question_missing_fact(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
) -> None:
    async def fake_answer(**_kwargs: Any) -> Any:
        yield {"event": "done", "data": {"text": "no idea"}}

    monkeypatch.setattr(ask_service, "answer", fake_answer)
    result = asyncio.run(
        eval_ask._run_question(
            {"q": "q", "facts": ["3 positions"]},
            deps=object(),
            client=object(),
            session_factory=session_factory,
        )
    )
    assert result["passed"] is False
    assert result["facts"] == [False]
