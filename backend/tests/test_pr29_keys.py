"""PR 29 — model provider keys (docs/SPEC.md → Model providers, Keys).

OpenAI and Anthropic are each optional with at least one required;
Remove is refused while the provider is in use; an unused provider key
removes cleanly and pauses its shadows. Every key here is fake; sockets
are blocked by conftest.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast

import app.config as config_module
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from app.api.main import create_app, run_migrations
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import Strategy
from app.engine.state import load_execution_state
from app.market.clock import make_clock
from app.secrets_service import FakeKeyTesters, SecretsStore
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.anyio

FAKE_OPENAI = "sk-fake-openai-0123456789abcdef"
FAKE_ANTHROPIC = "sk-ant-fake-key-0123456789abcdef"
FAKE_FINNHUB = "fake-finnhub-key-0123456789abcd"
FAKE_KEY_ID = "PKFAKEKEYID0000000001"
FAKE_SECRET = "fakealpacaSecret0123456789abcdef"
NOW = datetime(2026, 10, 19, 9, 0)

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


class _StubExecutor:
    def __init__(self) -> None:
        self.resumed = False

    async def resume_from_pause(self) -> None:
        self.resumed = True

    async def refresh_account(self) -> None:
        return None


def _paper_client() -> tuple[TestClient, _StubExecutor]:
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
    # Settings validation only touches the broker for universe changes.
    app.state.broker = object()
    return TestClient(app), executor


def _save_keys(client: TestClient, *groups: str) -> None:
    for group in groups:
        body = (
            {"key_id": FAKE_KEY_ID, "secret": FAKE_SECRET}
            if group == "alpaca"
            else {
                "value": {
                    "openai": FAKE_OPENAI,
                    "anthropic": FAKE_ANTHROPIC,
                    "finnhub": FAKE_FINNHUB,
                }[group]
            }
        )
        res = client.post(f"/api/keys/{group}", json=body)
        assert res.status_code == 200, res.text


def _strategy(name: str, model: str, status: str = "active") -> int:
    with get_session_maker()() as session:
        s = Strategy(
            name=name,
            kind="llm",
            prompt_version="decision.v1",
            model=model,
            temperature=0.0,
            status=status,
            created_at=NOW,
        )
        session.add(s)
        session.commit()
        return s.id


# ----------------------------------------------------------------------
# Resume needs a passing key for every provider in use — and only those.


def test_no_model_key_blocks_resume() -> None:
    client, executor = _paper_client()
    res = client.post("/api/pause/resume")
    assert res.status_code == 422
    assert "add an OpenAI or Anthropic key" in res.text
    assert not executor.resumed


def test_openai_only_key_resumes_when_nothing_uses_anthropic() -> None:
    client, executor = _paper_client()
    _save_keys(client, "alpaca", "openai", "finnhub")
    res = client.post("/api/pause/resume")
    assert res.status_code == 200, res.text
    assert executor.resumed


def test_review_task_on_missing_provider_blocks_resume() -> None:
    client, executor = _paper_client()
    _save_keys(client, "alpaca", "openai", "finnhub")
    config = get_config()
    tree = config.effective()
    tree["reviews"] = {
        **(tree.get("reviews") or {}),
        "models": {
            "weekly": "claude-haiku-5-5",
            "briefing": "gpt-4o-mini",
            "verdict": "gpt-4o-mini",
        },
    }
    config.set_pending(tree, 500)
    config.promote_pending()
    res = client.post("/api/pause/resume")
    assert res.status_code == 422
    assert "Anthropic key" in res.text
    assert not executor.resumed


def test_shadow_on_missing_provider_key_does_not_block_resume() -> None:
    client, executor = _paper_client()
    _strategy("Claude shadow", "claude-haiku-5-5")
    _save_keys(client, "alpaca", "openai", "finnhub")
    res = client.post("/api/pause/resume")
    assert res.status_code == 200, res.text
    assert executor.resumed


# ----------------------------------------------------------------------
# Remove — in use is refused; unused removes and pauses its shadows.


def test_remove_in_use_key_refused() -> None:
    client, _ = _paper_client()
    _save_keys(client, "openai")
    res = client.post("/api/keys/openai/remove")
    assert res.status_code == 422
    assert "live decisions use gpt-4o-mini" in res.text


def test_remove_key_in_use_by_review_refused() -> None:
    client, _ = _paper_client()
    _save_keys(client, "openai", "anthropic")
    config = get_config()
    tree = config.effective()
    tree["reviews"] = {
        **(tree.get("reviews") or {}),
        "models": {
            "weekly": "claude-haiku-5-5",
            "briefing": "gpt-4o-mini",
            "verdict": "gpt-4o-mini",
        },
    }
    config.set_pending(tree, 501)
    config.promote_pending()
    res = client.post("/api/keys/anthropic/remove")
    assert res.status_code == 422
    assert "weekly review" in res.text


def test_remove_unused_key_pauses_shadows() -> None:
    client, _ = _paper_client()
    sid = _strategy("Claude shadow", "claude-haiku-5-5")
    _strategy("GPT shadow", "gpt-4o-mini")
    _save_keys(client, "anthropic")
    res = client.post("/api/keys/anthropic/remove")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["paused_shadows"] == ["Claude shadow"]
    store = cast(Any, client.app).state.secrets_store
    assert store.get("anthropic") is None
    with get_session_maker()() as session:
        strategy = session.get(Strategy, sid)
        assert strategy is not None
        assert strategy.status == "paused"


def test_missing_model_key_is_refused_on_save() -> None:
    """A model whose provider has no passing key can't be picked."""
    client, _ = _paper_client()
    _save_keys(client, "openai")
    res = client.post(
        "/api/settings",
        json={
            "settings": {"decision": {"model": "claude-haiku-5-5"}},
            "reason": "try claude",
        },
    )
    assert res.status_code == 422
    assert "needs the Anthropic key" in res.text


# ----------------------------------------------------------------------
# migration: existing rows are OpenAI calls; request ids copied over.


def test_migration_fills_provider_and_provider_request_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "pr29.db"
    monkeypatch.setenv("SA_DB_PATH", str(db))
    monkeypatch.setattr(config_module, "_config", None)
    command.upgrade(AlembicConfig(str(ALEMBIC_INI)), "a8b9c0d1e2f3")
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO cycles (id, kind, scheduled_time, status) "
                "VALUES (1, 'cycle', '2026-10-22 15:46:00', 'done')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO decisions (id, cycle_id, symbol, context, "
                "final_action, model_output, prompt_version, prompt_hash, "
                "model, tokens, cost, cached) VALUES "
                "(1, 1, 'WMT', 'candidate', 'enter', '{}', 'decision.v1', "
                "'h', 'gpt-4o-mini', 10, '0', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO llm_calls (decision_id, sent_at, "
                "session_date, source, kind, model, "
                "temperature, prompt_version, prompt_hash, tokens_in, "
                "tokens_out, cost, cost_source, latency_ms, outcome, reasons, "
                "request_id) VALUES (1, '2026-10-22 15:46:01', '2026-10-22', "
                "'live', 'decision', "
                "'gpt-4o-mini', 0, 'decision.v1', 'h', 5, 5, '0', 'priced', 100, "
                "'success', '[]', 'req_openai_1')"
            )
        )
    command.upgrade(AlembicConfig(str(ALEMBIC_INI)), "head")
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT provider, provider_request_id, request_id FROM llm_calls")
        ).one()
        assert row[0] == "openai"
        assert row[1] == "req_openai_1"
        assert row[2] == "req_openai_1"
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(llm_cache)"))}
        assert {"provider", "effort"} <= cols
    engine.dispose()
