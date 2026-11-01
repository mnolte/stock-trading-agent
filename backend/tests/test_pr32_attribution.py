"""PR 32 remainder: the draft-label table, verdict facts per spec,
weekly groups/sample/suggestion, briefing status-line dedup and the
known-prompt-version 422 paths (docs/kickoffs/pr-32.md).
"""

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.config import AppConfig
from app.data.fake import FakeCalendarSource
from app.db.base import Base
from app.db.models import Cycle, Decision, Outcome, Position, Run, Verdict
from app.market.calendar import NY_TZ
from app.market.clock import UTC, FakeClock
from app.reviews.client import FakeReviewClient
from app.reviews.facts import checks_at_decision, verdict_facts, weekly_facts
from app.reviews.service import ReviewService
from app.reviews.templates import briefing_display, verdict_plain
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

MONDAY = date(2026, 10, 12)
SATURDAY = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


def _config(**reviews: object) -> AppConfig:
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["MSFT", "SPY", "AMZN", "MA", "HD"]
    for key, value in reviews.items():
        config.yaml.setdefault("reviews", {})[key] = value
    return config


def _service(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    client: FakeReviewClient | None = None,
    config: AppConfig | None = None,
) -> ReviewService:
    return ReviewService(
        client=client,
        config=config or _config(),
        clock=clock,
        calendar=FakeCalendarSource(),
        session_factory=session_factory,
    )


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def _ny(day: date, at: time = time(10, 46)) -> datetime:
    return datetime.combine(day, at, tzinfo=NY_TZ)


def _seed_decision(
    session: Session,
    scheduled_utc: datetime,
    *,
    symbol: str = "MSFT",
    model_action: str = "skip",
    final_action: str = "skip",
    option_id: str | None = None,
    options: list[dict[str, object]] | None = None,
    change_reason: str | None = None,
    change_detail: str | None = None,
    price: float = 420.0,
    indicators: bool = False,
    price_5sessions: str | None = "424.00",
    option_touches: dict[str, object] | None = None,
    first_touch: str | None = None,
    thesis: str | None = None,
) -> Decision:
    cycle = Cycle(
        kind="cycle",
        scheduled_time=_naive(scheduled_utc),
        started_at=_naive(scheduled_utc),
        finished_at=_naive(scheduled_utc + timedelta(minutes=1)),
        status="done",
    )
    session.add(cycle)
    session.flush()
    snapshot: dict[str, object] = {
        "symbol": symbol,
        "context": "candidate",
        "price": price,
        "regime": {"trend": "up", "volatility": "normal"},
    }
    if indicators:
        snapshot["indicators_1h"] = {
            "ema20": 430.0,
            "ema50": 425.0,
            "atr14": 2.0,
        }
        snapshot["indicators_daily"] = {
            "sma50": 400.0,
            "sma200": 380.0,
            "close": 390.0,
        }
        snapshot["levels"] = {"swing_highs": [440.0], "swing_lows": [400.0]}
    output: dict[str, object] = {"action": model_action, "confidence": 0.6}
    if option_id is not None:
        output["option_id"] = option_id
    if thesis is not None:
        output["thesis"] = thesis
    validation: dict[str, object] = {
        "valid": True,
        "model_action": model_action,
        "final_action": final_action,
        "changed_by_code": model_action != final_action,
    }
    if change_reason is not None:
        validation["reason"] = change_reason
        validation["checks"] = [
            {
                "name": "Reward to risk ≥ 1.5",
                "passed": False,
                "detail": change_detail or "1.4 below 1.5",
            }
        ]
    decision = Decision(
        cycle_id=cycle.id,
        symbol=symbol,
        context="candidate",
        input_snapshot=snapshot,
        rules_result={"status": "pass", "reason": None, "checks": []},
        model_output=output,
        validation_result=validation,
        final_action=final_action,
        prompt_version="decision.v2",
        prompt_hash="abc",
        model="fake-decision",
        cost=Decimal("0.001"),
        chosen_option=option_id if model_action == "enter" else None,
        options={"options": options} if options else None,
    )
    session.add(decision)
    session.flush()
    outcome = Outcome(decision_id=decision.id)
    if price_5sessions is not None:
        outcome.price_5sessions = Decimal(price_5sessions)
        outcome.price_5sessions_at = _naive(_ny(date(2026, 10, 22)))
    outcome.option_touches = option_touches
    outcome.first_touch = first_touch
    session.add(outcome)
    session.flush()
    return decision


def _seed_position(
    session: Session,
    entry: Decision,
    *,
    opened: datetime,
    closed: datetime | None = None,
    pnl: str = "45.00",
    exit_reason: str = "target",
) -> Position:
    position = Position(
        symbol=entry.symbol,
        origin="agent",
        entry_decision_id=entry.id,
        qty=10,
        avg_entry_price=Decimal("100.00"),
        current_stop_price=Decimal("97.00"),
        target_price=Decimal("106.00"),
        opened_at=_naive(opened),
        closed_at=_naive(closed) if closed else None,
        realized_pnl_raw=Decimal(pnl) if closed else None,
        exit_reason=exit_reason if closed else None,
    )
    session.add(position)
    return position


def _facts(
    session: Session, decision: Decision, scheduled: datetime
) -> dict[str, object]:
    outcome = session.execute(
        select(Outcome).where(Outcome.decision_id == decision.id)
    ).scalar_one()
    return verdict_facts(
        session,
        config=_config(),
        clock=FakeClock(SATURDAY),
        decision=decision,
        scheduled=scheduled,
        outcome=outcome,
    )


# ----------------------------------------------------------------------
# the SPEC draft-label table, one case per row


def _label_facts(
    action: str,
    *,
    option_id: str | None = "A",
    touch: object = None,
    options_touches: dict[str, object] | None = None,
    trade_touch: str | None = None,
    pct: float | None = None,
) -> dict[str, object]:
    options = (
        [
            {"option_id": oid, "rr": 2.0, "touch": t}
            for oid, t in (options_touches or {}).items()
        ]
        if options_touches is not None
        else []
    )
    return {
        "sessions": 5,
        "skip_flat_pct": 2,
        "model_action": action,
        "final_action": "enter" if action == "enter" else "skip",
        "option_id": option_id,
        "options": options,
        "trade": (
            {"first_touch": trade_touch, "r": None, "exit": None, "open": False}
            if trade_touch is not None or action == "enter" and not options
            else None
        ),
        "pct_5sessions": pct,
    }


def test_label_enter_target_first() -> None:
    facts = _label_facts("enter", touch=None, options_touches={"A": "target"})
    assert verdict_plain(facts)["verdict"] == "good_call"


def test_label_enter_stop_first() -> None:
    facts = _label_facts("enter", options_touches={"A": "stop"})
    assert verdict_plain(facts)["verdict"] == "bad_call"


@pytest.mark.parametrize("touch", ["neither", "not_filled", None])
def test_label_enter_unclear(touch: object) -> None:
    facts = _label_facts("enter", options_touches={"A": touch})
    assert verdict_plain(facts)["verdict"] == "unclear"


def test_label_enter_real_trade_first_touch() -> None:
    facts = _label_facts(
        "enter", option_id=None, options_touches=None, trade_touch="target"
    )
    assert verdict_plain(facts)["verdict"] == "good_call"
    facts = _label_facts(
        "enter", option_id=None, options_touches=None, trade_touch="stop"
    )
    assert verdict_plain(facts)["verdict"] == "bad_call"


def test_label_skip_options_target_first() -> None:
    facts = _label_facts(
        "skip", options_touches={"A": "stop", "B": "target", "C": "neither"}
    )
    assert verdict_plain(facts)["verdict"] == "bad_call"


def test_label_skip_options_all_stops_or_unfilled() -> None:
    facts = _label_facts(
        "skip", options_touches={"A": "stop", "B": "not_filled"}
    )
    assert verdict_plain(facts)["verdict"] == "good_call"


@pytest.mark.parametrize(
    "touches",
    [{"A": "neither", "B": "neither"}, {"A": None}, {"A": "stop", "B": "neither"}],
)
def test_label_skip_options_unclear(touches: dict[str, object]) -> None:
    facts = _label_facts("skip", options_touches=touches)
    assert verdict_plain(facts)["verdict"] == "unclear"


def test_label_skip_no_options_pct_band() -> None:
    assert (
        verdict_plain(_label_facts("skip", options_touches=None, pct=3.0))[
            "verdict"
        ]
        == "bad_call"
    )
    assert (
        verdict_plain(_label_facts("skip", options_touches=None, pct=-3.0))[
            "verdict"
        ]
        == "good_call"
    )
    assert (
        verdict_plain(_label_facts("skip", options_touches=None, pct=1.0))[
            "verdict"
        ]
        == "unclear"
    )


# ----------------------------------------------------------------------
# verdict facts: blocked enter, checks recompute, execution


async def test_verdict_facts_blocked_enter(
    session_factory: sessionmaker[Session],
) -> None:
    """MA, decision 232 shape: model enter, code skip for reward to risk
    1.4 < 1.5 — the verdict is about the enter, not a model skip."""
    scheduled = _ny(date(2026, 10, 15))
    with session_factory() as session:
        decision = _seed_decision(
            session,
            scheduled,
            symbol="MA",
            model_action="enter",
            final_action="skip",
            option_id="B",
            options=[
                {"id": "A", "entry_limit": 100, "stop": 97, "target": 106},
                {"id": "B", "entry_limit": 100, "stop": 99, "target": 101.4},
            ],
            change_reason="Reward to risk ≥ 1.5",
            change_detail="1.4 below 1.5",
            option_touches={"A": "stop", "B": "target"},
            indicators=True,
        )
        session.commit()
        facts = _facts(session, decision, scheduled)

    assert facts["model_action"] == "enter"
    assert facts["option_id"] == "B"
    assert facts["changed_by_code"] is True
    assert facts["change_reason"] == "Reward to risk ≥ 1.5"
    assert facts["change_text"] is not None
    assert "1.4" in str(facts["change_text"])
    assert facts["execution"] == "not_sent"
    checks = facts["checks_at_decision"]
    assert isinstance(checks, dict)
    assert checks["trend_1h"] == "mixed"  # 420 not > 430 > 425
    assert checks["trend_daily"] == "mixed"  # 390 between 380 and 400
    assert checks["regime_trend"] == "up"
    assert checks["regime_volatility"] == "normal"
    # limit 420.21; swing high 440 is 19.79 above → ~9.9 ATR.
    assert checks["room_atr"] == pytest.approx(9.9, abs=0.1)
    assert checks["room_level"] is not None
    # The chosen option B touched its target first → good call on the enter.
    assert facts["draft_verdict"] == "good_call"


def test_checks_at_decision_uses_stored_v3(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision = _seed_decision(session, _ny(date(2026, 10, 15)))
        decision.input_snapshot = {
            "price": 420.0,
            "checks": {"trend_1h": "down", "room_atr": 0.4},
        }
        session.commit()
        checks = checks_at_decision(decision, config=_config())
    assert checks == {"trend_1h": "down", "room_atr": 0.4}


async def test_verdict_execution_filled_and_not_filled(
    session_factory: sessionmaker[Session],
) -> None:
    scheduled = _ny(date(2026, 10, 15))
    with session_factory() as session:
        filled = _seed_decision(
            session,
            scheduled,
            symbol="AAPL",
            model_action="enter",
            final_action="enter",
            option_id="A",
            options=[{"id": "A", "entry_limit": 100, "stop": 97, "target": 106}],
            first_touch="target",
        )
        _seed_position(
            session,
            filled,
            opened=_ny(date(2026, 10, 15), time(11, 0)),
            closed=_ny(date(2026, 10, 20), time(15, 0)),
        )
        session.commit()
        facts = _facts(session, filled, scheduled)
        assert facts["execution"] == "filled"
        assert facts["draft_verdict"] == "good_call"


async def test_model_cannot_change_the_label(
    session_factory: sessionmaker[Session],
) -> None:
    """A model answer containing a verdict field is ignored: the label
    always comes from code's draft_verdict."""
    scheduled = _ny(date(2026, 10, 15))
    with session_factory() as session:
        _seed_decision(
            session,
            scheduled,
            symbol="AMZN",
            model_action="skip",
            option_touches={"A": "target"},
            options=[{"id": "A", "entry_limit": 100, "stop": 97, "target": 106}],
        )
        session.commit()
    service = _service(
        session_factory,
        FakeClock(datetime(2026, 10, 22, 12, 0, tzinfo=UTC)),
        client=FakeReviewClient(
            handler=lambda _s, _u: json.dumps(
                {
                    "verdict": "good_call",  # v1 shape — must not win
                    "reason": "Model skipped; A reached its target first.",
                    "thesis_check": "consistent",
                    "contradiction": None,
                }
            )
        ),
    )
    assert await service.write_due_verdicts() == 1
    with session_factory() as session:
        verdict = session.execute(select(Verdict)).scalar_one()
        # A target touch on a model skip is a bad call — code's label.
        assert verdict.verdict == "bad_call"
        assert verdict.thesis_check == "consistent"
        assert verdict.prompt_version == "verdict.v2"


async def test_thesis_contradiction_and_number_check_fallback(
    session_factory: sessionmaker[Session],
) -> None:
    """HD, decision 2324 shape: thesis "daily trend up" with trend_daily
    mixed → the model's contradicts is stored; a contradiction with an
    invented number falls back to the template, label unchanged."""
    scheduled = _ny(date(2026, 10, 15))
    with session_factory() as session:
        hd = _seed_decision(
            session,
            scheduled,
            symbol="HD",
            model_action="enter",
            final_action="enter",
            option_id="A",
            options=[{"id": "A", "entry_limit": 100, "stop": 97, "target": 106}],
            first_touch="stop",
            thesis="daily trend up",
            indicators=True,  # trend_daily is "mixed" in this snapshot
        )
        _seed_position(
            session,
            hd,
            opened=_ny(date(2026, 10, 15), time(11, 0)),
            closed=_ny(date(2026, 10, 16), time(15, 0)),
            pnl="-30.00",
            exit_reason="stop",
        )
        session.commit()
    service = _service(
        session_factory,
        FakeClock(datetime(2026, 10, 22, 12, 0, tzinfo=UTC)),
        client=FakeReviewClient(
            handler=lambda _s, _u: json.dumps(
                {
                    "reason": "Model entered on A; the stop was reached first.",
                    "thesis_check": "contradicts",
                    "contradiction": "says daily trend up; daily trend was mixed",
                }
            )
        ),
    )
    assert await service.write_due_verdicts() == 1
    with session_factory() as session:
        verdict = session.execute(select(Verdict)).scalar_one()
        assert verdict.thesis_check == "contradicts"
        assert "daily trend was mixed" in (verdict.contradiction or "")
        assert verdict.verdict == "bad_call"  # stop first → bad call

    # A contradiction holding an invented number fails the number check
    # → plain template reason, code's label, thesis_check null.
    from sqlalchemy import delete as sql_delete

    with session_factory() as session:
        session.execute(sql_delete(Verdict))
        hd2 = _seed_decision(
            session,
            _ny(date(2026, 10, 16)),
            symbol="HD2",
            model_action="enter",
            final_action="enter",
            option_id="A",
            options=[{"id": "A", "entry_limit": 100, "stop": 97, "target": 106}],
            first_touch="target",
            thesis="daily trend up",
            indicators=True,
        )
        _seed_position(
            session,
            hd2,
            opened=_ny(date(2026, 10, 16), time(11, 0)),
            closed=_ny(date(2026, 10, 20), time(15, 0)),
        )
        session.commit()
    service2 = _service(
        session_factory,
        FakeClock(datetime(2026, 10, 23, 12, 0, tzinfo=UTC)),
        client=FakeReviewClient(
            handler=lambda _s, _u: json.dumps(
                {
                    "reason": "Model entered on A; A reached its target first.",
                    "thesis_check": "contradicts",
                    "contradiction": "room was 7.7 ATR",  # invented number
                }
            )
        ),
    )
    # HD's deleted verdict is also re-drafted; both land as templates.
    assert await service2.write_due_verdicts() == 2
    with session_factory() as session:
        verdicts = session.execute(select(Verdict)).scalars().all()
        v2 = next(v for v in verdicts if v.decision.symbol == "HD2")
        assert v2.verdict == "good_call"
        assert v2.draft_source == "template"
        assert v2.thesis_check is None


# ----------------------------------------------------------------------
# weekly: groups, sample, suggestion


def _seed_confirmed(
    session: Session,
    decision: Decision,
    verdict: str,
    *,
    updated: datetime,
    thesis_check: str | None = None,
    model_action: str | None = None,
    changed_by_code: bool | None = None,
    change_reason: str | None = None,
) -> None:
    facts: dict[str, object] = {}
    if model_action is not None:
        facts["model_action"] = model_action
    if changed_by_code is not None:
        facts["changed_by_code"] = changed_by_code
    if change_reason is not None:
        facts["change_reason"] = change_reason
    session.add(
        Verdict(
            decision_id=decision.id,
            verdict=verdict,
            reason="x",
            source="marc",
            thesis_check=thesis_check,
            facts=facts or None,
            updated_at=_naive(updated),
            created_at=_naive(updated),
        )
    )


async def test_weekly_verdict_groups_and_sample(
    session_factory: sessionmaker[Session],
) -> None:
    """Confirmed verdicts land in three groups; a blocked enter is not a
    model skip; thesis contradictions and the sample are counted."""
    when = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    with session_factory() as session:
        session.add(
            Run(started_at=_naive(datetime(2026, 10, 5)), starting_equity=Decimal("10000"))
        )
        executed = _seed_decision(
            session, _ny(date(2026, 10, 13)), symbol="AAPL",
            model_action="enter", final_action="enter",
        )
        _seed_position(
            session, executed,
            opened=_ny(date(2026, 10, 13), time(11, 0)),
            closed=_ny(date(2026, 10, 15), time(15, 0)),
        )
        blocked = _seed_decision(
            session, _ny(date(2026, 10, 14)), symbol="MA",
            model_action="enter", final_action="skip",
            option_id="B", options=[{"id": "B", "entry_limit": 1, "stop": 1, "target": 1}],
            change_reason="Reward to risk ≥ 1.5",
        )
        model_skip = _seed_decision(
            session, _ny(date(2026, 10, 14)), symbol="AMZN",
            model_action="skip", final_action="skip", price=200.0,
            price_5sessions="206.00",
        )
        _seed_confirmed(
            session, executed, "good_call", updated=when,
            model_action="enter", changed_by_code=False,
        )
        _seed_confirmed(
            session, blocked, "good_call", updated=when,
            model_action="enter", changed_by_code=True,
            change_reason="Reward to risk ≥ 1.5",
            thesis_check="contradicts",
        )
        _seed_confirmed(
            session, model_skip, "bad_call", updated=when,
            model_action="skip",
        )
        session.commit()
        facts = weekly_facts(
            session,
            config=_config(),
            clock=FakeClock(SATURDAY),
            calendar=FakeCalendarSource(),
            monday=MONDAY,
        )

    verdicts = facts["verdicts"]
    assert verdicts["entered"] == {"good_call": 1, "bad_call": 0, "unclear": 0}
    assert verdicts["blocked"]["good_call"] == 1
    assert verdicts["blocked"]["reasons"] == {"Reward to risk ≥ 1.5": 1}
    assert verdicts["skips"] == {"good_call": 0, "bad_call": 1, "unclear": 0}
    assert verdicts["thesis_contradictions"] == 1
    assert verdicts["total"] == 3
    # The blocked enter's final action is skip, but the model answered
    # enter — it must not land in the model-skips bucket.
    assert facts["skips"]["total"] == 1
    assert facts["skips"]["up"] == 1  # 200 → 206 = +3%
    sample = facts["sample"]
    assert sample["closed_trades"]["week"] == 1
    assert sample["closed_trades"]["run"] == 1
    assert sample["model_skips_with_outcome"]["week"] == 1
    assert sample["confirmed_verdicts"]["week"] == 3
    assert facts["suggestion_allowed"] is False  # 1 closed trade in the run


async def test_weekly_suggestion_dropped_and_kept(
    session_factory: sessionmaker[Session],
) -> None:
    """1 closed trade → suggestion_allowed false and a model suggestion
    is dropped (INFO); 10 closed trades → it is kept with its evidence."""
    now = SATURDAY
    with session_factory() as session:
        entry = _seed_decision(
            session, _ny(date(2026, 10, 13)), model_action="enter",
            final_action="enter",
        )
        _seed_position(
            session, entry,
            opened=_ny(date(2026, 10, 13), time(11, 0)),
            closed=_ny(date(2026, 10, 15), time(15, 0)),
        )
        session.commit()
    suggestion = {
        "text": "Skip high-volatility entries.",
        "evidence": "The high-volatility trades all lost.",
    }
    service = _service(
        session_factory,
        FakeClock(now),
        client=FakeReviewClient(
            handler=lambda _s, _u: json.dumps(
                {
                    "summary": "A quiet week.",
                    "observations": [],
                    "suggestion": suggestion,
                }
            )
        ),
        config=_config(),
    )
    review = await service.write_weekly_for(now)
    assert review is not None
    assert (review.content or {}).get("suggestion") is None

    with session_factory() as session:
        # Nine more closed trades anywhere in the run.
        for i in range(9):
            e = _seed_decision(
                session,
                _ny(date(2026, 10, 14), time(10, 46)) + timedelta(minutes=i * 5),
                symbol=f"S{i}",
                model_action="enter",
                final_action="enter",
            )
            _seed_position(
                session, e,
                opened=_ny(date(2026, 10, 14), time(11, 0)),
                closed=_ny(date(2026, 10, 15), time(15, 0)),
            )
        session.commit()
    review2 = await _service(
        session_factory,
        FakeClock(now + timedelta(days=7)),
        client=FakeReviewClient(
            handler=lambda _s, _u: json.dumps(
                {
                    "summary": "A busy week.",
                    "observations": [],
                    "suggestion": suggestion,
                }
            )
        ),
    ).write_weekly_for(now + timedelta(days=7))
    assert review2 is not None
    kept = (review2.content or {}).get("suggestion")
    assert isinstance(kept, dict)
    assert kept["text"] == suggestion["text"]
    assert kept["evidence"] == suggestion["evidence"]


# ----------------------------------------------------------------------
# briefing: status line stored separately, dedup on read


async def test_briefing_stores_status_line_separately(
    session_factory: sessionmaker[Session],
) -> None:
    service = _service(
        session_factory,
        FakeClock(_ny(date(2026, 10, 22))),
        client=FakeReviewClient(
            handler=lambda _s, _u: json.dumps({"text": "Watch the market."})
        ),
    )
    review = await service.write_briefing_for(date(2026, 10, 22))
    assert review is not None
    assert review.text == "Watch the market."  # no prefix in stored text
    status, text = briefing_display(review)
    assert status is not None and status.startswith("Positions:")
    assert text == "Watch the market."


def test_briefing_display_strips_old_prefix() -> None:
    """Rows written before the split kept the status line inside text —
    the display strips the leading copy once."""
    from app.db.models import Review

    status = "Positions: none. Earnings today: none; tomorrow: none. Banners: none."
    old = Review(
        kind="briefing",
        period="2026-10-15",
        facts={"positions": [], "earnings_today": [], "earnings_tomorrow": [], "banners": []},
        text=f"{status} Quiet day ahead.",
        content={"text": f"{status} Quiet day ahead.", "status_line": status},
        source="model",
        due_at=datetime(2026, 10, 15, 13, 0),
        created_at=datetime(2026, 10, 15, 13, 0),
    )
    got_status, text = briefing_display(old)
    assert got_status == status
    assert text == "Quiet day ahead."


# ----------------------------------------------------------------------
# API: verdict row fields


def test_verdicts_api_carries_call_change_and_thesis(
    client: TestClient,
) -> None:
    from app.db.base import get_session_maker

    with get_session_maker()() as session:
        decision = _seed_decision(
            session,
            _ny(date(2026, 10, 15)),
            symbol="MA",
            model_action="enter",
            final_action="skip",
            option_id="B",
            options=[{"id": "B", "entry_limit": 100, "stop": 99, "target": 101.4}],
            change_reason="Reward to risk ≥ 1.5",
            change_detail="1.4 below 1.5",
            option_touches={"B": "target"},
        )
        session.add(
            Verdict(
                decision_id=decision.id,
                verdict="good_call",
                reason="Model entered on B; code blocked it; B hit its target.",
                thesis_check="contradicts",
                contradiction="says daily trend up; daily trend was mixed",
                prompt_version="verdict.v2",
                source="draft",
                draft_source="model",
                created_at=datetime(2026, 10, 22, 12, 0),
                updated_at=datetime(2026, 10, 22, 12, 0),
            )
        )
        session.commit()
        decision_id = decision.id

    items = client.get("/api/verdicts?status=draft").json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["symbol"] == "MA"
    assert item["model_action"] == "enter"
    assert item["option_id"] == "B"
    assert item["final_action"] == "skip"
    assert item["changed_by_code"] is True
    assert item["change_reason"] == "Reward to risk ≥ 1.5"
    assert item["change_text"] is not None
    assert item["outcome"] == "option B reached its target first"
    assert item["label_by_code"] is True
    assert item["thesis_check"] == "contradicts"
    assert item["contradiction"] is not None

    # The decision drawer shows the same call, change and thesis check.
    detail = client.get(f"/api/decisions/{decision_id}").json()
    verdict = detail["verdict"]
    assert verdict["model_action"] == "enter"
    assert verdict["option_id"] == "B"
    assert verdict["changed_by_code"] is True
    assert verdict["thesis_check"] == "contradicts"
    assert verdict["label_by_code"] is True


def test_verdicts_api_v1_row_has_no_thesis(client: TestClient) -> None:
    """A v1-era draft: the call and code line are built from the
    decision; there is no thesis check and no label-by-code tag."""
    from app.db.base import get_session_maker

    with get_session_maker()() as session:
        decision = _seed_decision(
            session,
            _ny(date(2026, 10, 15)),
            symbol="AMZN",
            model_action="skip",
            final_action="skip",
            price_5sessions="206.00",
            price=200.0,
        )
        session.add(
            Verdict(
                decision_id=decision.id,
                verdict="bad_call",
                reason="Rose after the skip.",
                source="draft",
                draft_source="model",
                created_at=datetime(2026, 10, 22, 12, 0),
                updated_at=datetime(2026, 10, 22, 12, 0),
            )
        )
        session.commit()

    items = client.get("/api/verdicts?status=draft").json()["items"]
    item = next(i for i in items if i["symbol"] == "AMZN")
    assert item["model_action"] == "skip"
    assert item["changed_by_code"] is False
    assert item["thesis_check"] is None
    assert item["label_by_code"] is False
    assert "+3.0% after 5 sessions" in (item["outcome"] or "")


# ----------------------------------------------------------------------
# known prompt versions on save (settings + backtest)


async def test_settings_save_refuses_unknown_prompt() -> None:
    from typing import cast

    from app.broker.base import Broker
    from app.settings_service import SettingsError, validate_settings

    try:
        await validate_settings(
            {"decision": {"prompt": "decision.v99"}},
            {},
            broker=cast(Broker, None),  # unused without a universe save
            held_symbols=set(),
        )
    except SettingsError as exc:
        assert any("unknown_prompt_version" in e for e in exc.errors)
    else:
        raise AssertionError("decision.v99 must be refused")


def test_backtest_resolve_refuses_unknown_prompt() -> None:
    """A strategy row naming a version without a contract → 422."""
    from typing import cast

    from app.api.backtests import _resolve_strategies
    from app.db.base import get_session_maker
    from app.db.models import Strategy
    from fastapi import HTTPException, Request

    with get_session_maker()() as session:
        session.add(
            Strategy(
                name="bad",
                kind="llm",
                status="active",
                prompt_version="decision.v99",
                model="gpt-4o-mini",
                temperature=0,
                created_at=datetime(2026, 10, 22, 12, 0),
            )
        )
        session.commit()
        sid = session.execute(select(Strategy.id)).scalar_one()

    with get_session_maker()() as session:
        try:
            _resolve_strategies(
                cast(Request, object()), session, [f"strategy:{sid}"]
            )
        except HTTPException as exc:
            assert exc.status_code == 422
            assert "unknown_prompt_version" in str(exc.detail)
        else:
            raise AssertionError("decision.v99 must be refused")


# ----------------------------------------------------------------------
# old weekly.v1 rows still display


def test_v1_suggestion_string_shape_parses(
    session_factory: sessionmaker[Session],
) -> None:
    """weekly.v1 stored a plain string suggestion — it must still render."""
    service = _service(session_factory, FakeClock(SATURDAY))
    content = service._validate(
        "weekly",
        {"summary": "Week recap.", "observations": [], "suggestion": "Try X."},
    )
    assert content is not None
    assert content["suggestion"] == "Try X."
