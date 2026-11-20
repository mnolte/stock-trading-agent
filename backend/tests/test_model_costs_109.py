"""Item 109 — model cost data: cache columns, cache_saving, the
Insights model-costs endpoint, backtest model_usage and the new
settings keys (docs/SPEC.md → Model costs, LLM call history → Storage,
Settings screen → Every model setting has a control)."""

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import AskMessage, AskThread, Backtest, Bar1d, LlmCall
from app.insights.model_costs import model_costs
from app.inspector.record import build_llm_call
from app.llm.base import ModelResponse, cache_saving, response_cache_saving
from fastapi.testclient import TestClient
from sqlalchemy import delete


def _table() -> dict[str, Any]:
    return {
        "gpt-4.1-mini": {
            "provider": "openai",
            "input": 0.40,
            "output": 1.60,
            "cached_input": 0.10,
        },
        "claude-haiku-5-5": {
            "provider": "anthropic",
            "input": 0.10,
            "output": 0.50,
            "cached_input": 0.01,
            "cache_write": 0.125,
        },
        "no-cache-price": {"provider": "openai", "input": 1.0, "output": 2.0},
    }


class TestCacheSaving:
    def test_openai_cached_reads(self) -> None:
        # 1M cached reads save (0.40 - 0.10) = $0.30.
        assert cache_saving(_table(), "gpt-4.1-mini", 1_000_000, 0) == Decimal(
            "0.30"
        )

    def test_anthropic_reads_and_writes(self) -> None:
        # 1M reads save 0.09; 1M writes cost (0.125 - 0.10) = 0.025 extra.
        assert cache_saving(_table(), "claude-haiku-5-5", 1_000_000, 0) == Decimal(
            "0.09"
        )
        assert cache_saving(_table(), "claude-haiku-5-5", 0, 1_000_000) == Decimal(
            "-0.025"
        )

    def test_writes_larger_than_reads_go_negative(self) -> None:
        value = cache_saving(_table(), "claude-haiku-5-5", 100, 1_000_000)
        assert value is not None and value < 0

    def test_no_price_returns_none(self) -> None:
        assert cache_saving(_table(), "missing-model", 10, 0) is None

    def test_no_cached_input_price_saves_zero_on_reads(self) -> None:
        # cached_input falls back to input → the read term is 0.
        assert cache_saving(_table(), "no-cache-price", 500, 0) == Decimal("0")


class TestBuildLlmCallCacheColumns:
    def _response(self, **kw: Any) -> ModelResponse:
        return ModelResponse(
            raw="{}",
            model="gpt-4.1-mini",
            tokens_in=1000,
            tokens_out=50,
            latency_ms=10,
            cached_tokens_in=kw.get("cached", 400),
            cache_write_tokens_in=kw.get("write"),
        )

    def test_stores_cache_columns(self) -> None:
        with get_session_maker()() as session:
            row = build_llm_call(
                source="live",
                kind="decision",
                sent_at=datetime(2026, 10, 22, 15, 0),
                response=self._response(cached=400),
                cache_saved=Decimal("0.0001"),
            )
            session.add(row)
            session.commit()
            assert row.tokens_cached_in == 400
            assert row.tokens_cache_write_in is None
            assert row.cache_saved == Decimal("0.0001")
            session.delete(row)
            session.commit()

    def test_older_shaped_response_stores_nulls(self) -> None:
        response = ModelResponse(
            raw=None, model="m", tokens_in=None, tokens_out=None, latency_ms=1
        )
        assert response.cached_tokens_in is None
        assert response_cache_saving(response, get_config()) is None
        with get_session_maker()() as session:
            row = build_llm_call(
                source="live",
                kind="decision",
                sent_at=datetime(2026, 10, 22, 15, 0),
                response=response,
                cache_saved=response_cache_saving(response, get_config()),
            )
            session.add(row)
            session.commit()
            assert row.tokens_cached_in is None
            assert row.tokens_cache_write_in is None
            assert row.cache_saved is None
            session.delete(row)
            session.commit()


def _seed_call(session: Any, **kw: Any) -> LlmCall:
    defaults: dict[str, Any] = {
        "sent_at": datetime(2026, 12, 10, 15, 0),
        "session_date": date(2026, 12, 10),
        "source": "live",
        "kind": "decision",
        "model": "gpt-4.1-mini",
        "prompt_version": "decision.v1",
        "tokens_in": 1000,
        "tokens_out": 100,
        "cost": Decimal("0.001"),
        "cost_source": "priced",
        "outcome": "success",
        "reasons": [],
    }
    call = LlmCall(**{**defaults, **kw})
    session.add(call)
    return call


@pytest.fixture()
def cost_fixture(client: TestClient) -> Iterator[dict[str, Any]]:
    """Calls for every purpose plus sessions and two backtests."""
    with get_session_maker()() as session:
        session.execute(delete(LlmCall))
        session.execute(delete(Backtest))
        session.execute(delete(Bar1d))
        session.execute(delete(AskMessage))
        session.execute(delete(AskThread))
        # Two NY sessions in December.
        for d in (date(2026, 12, 9), date(2026, 12, 10)):
            session.add(
                Bar1d(
                    symbol="SPY", date=d,
                    open=Decimal(1), high=Decimal(1), low=Decimal(1),
                    close=Decimal(1), volume=1,
                )
            )
        # Live: one call this month, one last month (boundary check).
        _seed_call(session)
        _seed_call(
            session,
            sent_at=datetime(2026, 11, 30, 20, 0),
            session_date=date(2026, 11, 30),
            cost=Decimal("0.5"),
        )
        # Shadow + cached columns on a pre-109-style row (nulls).
        _seed_call(
            session, source="shadow", kind="decision", model="claude-haiku-5-5",
            tokens_in=2000, tokens_cached_in=1500, tokens_cache_write_in=100,
            cache_saved=Decimal("0.0002"), cost=Decimal("0.002"),
        )
        _seed_call(session, source="review", kind="weekly", model="gpt-4o-mini")
        _seed_call(session, source="review", kind="briefing", model="gpt-4o-mini")
        _seed_call(session, source="review", kind="verdict", model="gpt-4o-mini")
        thread = AskThread(
            title="t", created_at=datetime(2026, 12, 10, 14, 0),
            updated_at=datetime(2026, 12, 10, 14, 0),
        )
        session.add(thread)
        session.flush()
        msg_ids = []
        for _ in range(2):
            msg = AskMessage(
                thread_id=thread.id, role="assistant", text="a",
                created_at=datetime(2026, 12, 10, 15, 0),
            )
            session.add(msg)
            session.flush()
            msg_ids.append(msg.id)
        _seed_call(
            session, source="ask", kind="ask", model="gpt-6.1-sol",
            ask_message_id=msg_ids[0], sent_at=datetime(2026, 12, 11, 15, 0),
            session_date=date(2026, 12, 11), cost=Decimal("0.01"),
        )
        _seed_call(
            session, source="ask", kind="ask", model="gpt-6.1-sol",
            ask_message_id=msg_ids[0], sent_at=datetime(2026, 12, 11, 15, 5),
            session_date=date(2026, 12, 11), cost=Decimal("0.01"),
        )
        _seed_call(
            session, source="ask", kind="ask", model="gpt-6.1-sol",
            ask_message_id=msg_ids[1], sent_at=datetime(2026, 12, 11, 15, 10),
            session_date=date(2026, 12, 11), cost=Decimal("0.02"),
        )
        # Backtests: one with model_usage, one without (old run).
        session.add_all([
            Backtest(
                name="run-a", start_date=date(2026, 1, 1), end_date=date(2026, 6, 1),
                universe_mode="fixed", strategies=[], starting_equity=Decimal(10000),
                slippage_pct=Decimal("0.05"), status="done", cost_usd=Decimal("3.00"),
                run_dir="data/backtests/run-1",
                summary={"model_usage": [{
                    "model": "gpt-4.1-mini", "calls": 10, "tokens_in": 5000,
                    "tokens_cached_in": 2000, "tokens_cache_write_in": 0,
                    "tokens_out": 500, "cost": "1.50", "cache_saved": "0.0006",
                    "answers_reused": 4,
                }]},
                created_at=datetime(2026, 12, 5, 12, 0),
                finished_at=datetime(2026, 12, 5, 18, 0),
            ),
            Backtest(
                name="run-b", start_date=date(2026, 1, 1), end_date=date(2026, 6, 1),
                universe_mode="fixed", strategies=[], starting_equity=Decimal(10000),
                slippage_pct=Decimal("0.05"), status="cancelled",
                cost_usd=Decimal("1.50"), run_dir="data/backtests/run-2",
                created_at=datetime(2026, 12, 6, 12, 0),
                finished_at=datetime(2026, 12, 6, 18, 0),
            ),
        ])
        session.commit()
    yield {}


class TestModelCosts:
    def test_period_split_and_totals(
        self, client: TestClient, cost_fixture: dict[str, Any]
    ) -> None:
        today = date(2026, 12, 15)
        with get_session_maker()() as session:
            data = model_costs(get_config(), session, "this_month", today=today)
        purposes = {p["key"]: p for p in data["purposes"]}
        # this_month: the Nov 30 call is out; everything else is in.
        assert purposes["live"]["calls"] == 1
        assert Decimal(purposes["live"]["cost"]) == Decimal("0.001")
        # Shadow cache columns aggregate.
        assert purposes["shadow"]["tokens_cached_in"] == 1500
        assert Decimal(purposes["shadow"]["cache_saved"]) == Decimal("0.0002")
        # Weekly cap covers all three review kinds.
        reviews_cost = sum(
            (Decimal(purposes[k]["cost"]) for k in ("weekly", "briefing", "verdict")),
            Decimal(0),
        )
        assert purposes["weekly"]["cap"]["kind"] == "monthly"
        assert Decimal(purposes["weekly"]["cap"]["used"]) == reviews_cost
        # Ask: 2 questions, 3 calls.
        assert purposes["ask"]["facts"]["questions"] == 2
        assert purposes["ask"]["calls"] == 3
        # Backtests: cost_usd of both runs; calls only from model_usage.
        assert Decimal(purposes["backtest"]["cost"]) == Decimal("4.50")
        assert purposes["backtest"]["calls"] == 10
        assert purposes["backtest"]["facts"]["answers_reused"] == 4
        assert purposes["backtest"]["facts"]["runs_without_model_usage"] == 1
        assert purposes["backtest"]["per_session"] is None
        # Total excludes nothing but backtests stay out of per_session.
        total = purposes["total"]
        assert total["calls"] == 1 + 1 + 3 + 3 + 10
        # By-model rows carry prices and purposes.
        by_id = {m["id"]: m for m in data["models"]}
        assert by_id["gpt-4.1-mini"]["input"] == 0.40
        assert "backtest" in by_id["gpt-4.1-mini"]["purposes"]
        assert by_id["claude-haiku-5-5"]["cached_input"] == 0.01
        # Cost per session: 2 sessions, buckets exclude backtests.
        assert len(data["per_session"]) == 2
        # Answer sentence exists and names the totals.
        assert "calls" in data["answer"]

    def test_last_month_excludes_december(
        self, client: TestClient, cost_fixture: dict[str, Any]
    ) -> None:
        with get_session_maker()() as session:
            data = model_costs(
                get_config(), session, "last_month", today=date(2026, 12, 15)
            )
        purposes = {p["key"]: p for p in data["purposes"]}
        assert purposes["live"]["calls"] == 1
        assert Decimal(purposes["live"]["cost"]) == Decimal("0.5")
        assert purposes["backtest"]["calls"] == 0

    def test_api_period_validation(
        self, client: TestClient, cost_fixture: dict[str, Any]
    ) -> None:
        ok = client.get("/api/insights/model-costs", params={"period": "all"})
        assert ok.status_code == 200
        assert ok.json()["period"]["name"] == "all"
        bad = client.get(
            "/api/insights/model-costs", params={"period": "year"}
        )
        assert bad.status_code == 422


class TestSettingsNewKeys:
    def _save(self, client: TestClient, settings: dict[str, Any]) -> Any:
        return client.post(
            "/api/settings",
            json={"settings": settings, "reason": "109 test"},
        )

    @pytest.mark.parametrize(
        ("section", "key", "good", "bad"),
        [
            ("decision", "call_timeout_seconds", 60, 500),
            ("decision", "max_output_tokens", 4096, 99999),
            ("reviews", "temperature", 1.0, 3.0),
            ("ask", "max_tool_rounds", 12, 0),
            ("ask", "max_context_tokens", 90000, 1000),
            ("ask", "max_tool_result_tokens", 8000, 100),
            ("ask", "answer_timeout_seconds", 120, 5),
            ("ask", "max_questions_per_minute", 20, 0),
            ("ask", "max_notes", 200, 5),
            ("ask", "max_output_tokens", 8192, 100),
            ("shadows", "monthly_cost_cap_usd", 10.0, 500.0),
            ("backtest", "monthly_cost_cap_usd", 50.0, 999.0),
        ],
    )
    def test_range_accept_and_reject(
        self,
        client: TestClient,
        section: str,
        key: str,
        good: Any,
        bad: Any,
    ) -> None:
        resp = self._save(client, {section: {key: good}})
        # Raising a limit or cap asks "Confirm it is you" where the test
        # client can't confirm — tightening always saves.
        assert resp.status_code in (200, 403), resp.text
        if resp.status_code == 403:
            assert resp.json()["error"]["code"] == "confirm_required"
        resp = self._save(client, {section: {key: bad}})
        assert resp.status_code == 422, resp.text

    def test_reviews_max_words_submap(self, client: TestClient) -> None:
        resp = self._save(
            client,
            {"reviews": {"max_words": {"weekly_summary": 200}}},
        )
        assert resp.status_code == 200, resp.text
        resp = self._save(
            client,
            {"reviews": {"max_words": {"weekly_summary": 10}}},
        )
        assert resp.status_code == 422

    def test_direction_tags(self) -> None:
        from app.settings_service import classify

        def tree(**kw: Any) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for path, value in kw.items():
                node = out
                parts = path.split(".")
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                node[parts[-1]] = value
            return out

        def direction(path: str, old: Any, new: Any) -> str:
            changes = classify(tree(**{path: old}), tree(**{path: new}))
            return str(changes[0]["direction"])

        assert direction("ask.max_tool_rounds", 8, 12) == "looser"
        assert direction("ask.max_tool_rounds", 8, 4) == "tighter"
        assert direction("decision.call_timeout_seconds", 30, 60) == "looser"
        assert direction("shadows.monthly_cost_cap_usd", 5, 9) == "looser"
        assert direction("backtest.monthly_cost_cap_usd", 20, 10) == "tighter"
        assert direction("reviews.temperature", 0.2, 1.0) == "neutral"
        assert direction("reviews.max_words.weekly_summary", 120, 200) == "neutral"

    def test_settings_models_read_only(self, client: TestClient) -> None:
        resp = client.get("/api/settings/models")
        assert resp.status_code == 200
        models = {m["id"]: m for m in resp.json()["models"]}
        assert "gpt-4o-mini" in models  # every models-block entry
        haiku = models["claude-haiku-5-5"]
        assert haiku["provider"] == "anthropic"
        assert haiku["cached_input"] == 0.01
        assert haiku["cache_write"] == 0.125
        assert haiku["effort"] == "low"
        assert haiku["cutoff"] == "2026-06-30"
        assert haiku["key_ok"] is True  # simulated mode needs no key
        assert isinstance(haiku["used_by"], list)
        assert haiku["timeout_seconds"] > 0


class TestBacktestModelUsage:
    def test_model_usage_sums_run_calls(self) -> None:
        """_model_usage groups a run's llm_calls per model and counts
        cached decisions as answers_reused."""
        import tempfile
        from pathlib import Path

        from app.backtests.bundle import init_strategy_db
        from app.backtests.store import session_maker_for
        from app.backtests.worker import _model_usage

        config = get_config()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "strat.db"
            init_strategy_db(db)
            sf = session_maker_for(db, config)
            with sf() as session:
                session.add_all([
                    LlmCall(
                        sent_at=datetime(2026, 1, 5, 15, 0),
                        session_date=date(2026, 1, 5),
                        source="live", kind="decision",
                        model="gpt-4.1-mini", tokens_in=100,
                        tokens_cached_in=50, tokens_out=10,
                        cost=Decimal("0.001"), cache_saved=Decimal("0.0001"),
                        cost_source="priced", outcome="success", reasons=[],
                    ),
                    LlmCall(
                        sent_at=datetime(2026, 1, 5, 15, 1),
                        session_date=date(2026, 1, 5),
                        source="live", kind="decision",
                        model="gpt-4.1-mini", tokens_in=200,
                        tokens_out=20, cost=Decimal("0.002"),
                        cost_source="priced", outcome="success", reasons=[],
                    ),
                ])
                session.commit()
            run = type("R", (), {"strategies": [{"slug": "strat"}]})()
            usage = _model_usage(Path(tmp), config, run)
        by_model = {u["model"]: u for u in usage}
        entry = by_model["gpt-4.1-mini"]
        assert entry["calls"] == 2
        assert entry["tokens_in"] == 300
        assert entry["tokens_cached_in"] == 50
        assert Decimal(entry["cost"]) == Decimal("0.003")
        assert Decimal(entry["cache_saved"]) == Decimal("0.0001")
