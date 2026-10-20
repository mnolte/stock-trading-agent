"""Reviews: facts, number check, fallbacks, cost cap, catch-up, verdicts.

Code computes every fact; the model only writes text. When the text is
unusable the plain template is shown with the reason, and every fallback
is logged. A draft verdict exists only once the 5-session outcome is in,
only for enters and model skips — never for rules no_calls — and only
confirmed verdicts count in the weekly review.
"""

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.config import AppConfig
from app.data.fake import FakeCalendarSource
from app.db.base import Base
from app.db.models import (
    Cycle,
    Decision,
    EquitySnapshot,
    Event,
    Outcome,
    Position,
    Review,
    Run,
    Verdict,
)
from app.market.calendar import NY_TZ
from app.market.clock import UTC, FakeClock
from app.reviews.client import FakeReviewClient
from app.reviews.facts import verdict_eligible, weekly_facts
from app.reviews.numbercheck import bad_numbers
from app.reviews.service import ReviewService
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

# Saturday 17 Oct 2026 10:00 Amsterdam (CEST = UTC+2) → 08:00 UTC.
SATURDAY = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
MONDAY = date(2026, 10, 12)


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


def _config(**reviews: object) -> AppConfig:
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["MSFT", "SPY", "AMZN"]
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
    context: str = "candidate",
    final_action: str = "skip",
    no_call_reason: str | None = None,
    price: float = 420.0,
    stop: float | None = None,
    confidence: float = 0.6,
    regime: dict[str, str] | None = None,
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
    decision = Decision(
        cycle_id=cycle.id,
        symbol=symbol,
        context=context,
        input_snapshot={
            "symbol": symbol,
            "price": price,
            "regime": regime or {"trend": "up", "volatility": "normal"},
        },
        rules_result={"status": "pass", "reason": None, "checks": []},
        model_output=(
            {
                "action": final_action,
                "confidence": confidence,
                "stop_price": stop,
            }
            if no_call_reason is None
            else None
        ),
        validation_result={
            "valid": no_call_reason is None,
            "model_action": final_action,
            "final_action": final_action,
            "changed_by_code": False,
        },
        final_action=final_action,
        no_call_reason=no_call_reason,
        prompt_version="decision.v1",
        prompt_hash="abc",
        model="none" if no_call_reason else "fake-decision",
        cost=Decimal("0.001") if no_call_reason is None else None,
    )
    session.add(decision)
    session.flush()
    session.add(Outcome(decision_id=decision.id))
    session.flush()
    return decision


def _seed_position(
    session: Session,
    entry: Decision,
    *,
    opened: datetime,
    closed: datetime,
    qty: int = 10,
    entry_price: str = "100.00",
    pnl: str = "150.00",
    exit_reason: str = "target",
) -> Position:
    position = Position(
        symbol=entry.symbol,
        origin="agent",
        entry_decision_id=entry.id,
        qty=qty,
        avg_entry_price=Decimal(entry_price),
        current_stop_price=Decimal("97.00"),
        target_price=Decimal("106.00"),
        opened_at=_naive(opened),
        closed_at=_naive(closed),
        realized_pnl_raw=Decimal(pnl),
        realized_pnl_adjusted=Decimal(pnl),
        exit_reason=exit_reason,
    )
    session.add(position)
    return position


def _seed_snapshot(
    session: Session,
    at: datetime,
    equity: str,
    spy: str | None = None,
) -> None:
    session.add(
        EquitySnapshot(
            time=_naive(at),
            equity=Decimal(equity),
            cash=Decimal(equity),
            spy_benchmark_value=Decimal(spy) if spy else None,
        )
    )


def _reviews(session_factory: sessionmaker[Session]) -> list[Review]:
    with session_factory() as session:
        return list(session.execute(select(Review)).scalars())


def _verdicts(session_factory: sessionmaker[Session]) -> list[Verdict]:
    with session_factory() as session:
        return list(session.execute(select(Verdict)).scalars())


def _fallback_events(session_factory: sessionmaker[Session]) -> list[Event]:
    with session_factory() as session:
        return list(
            session.execute(
                select(Event).where(Event.event_type == "review_fallback")
            ).scalars()
        )


# ----------------------------------------------------------------------
# facts from fixture data


def _seed_week(session: Session) -> None:
    """One full fixture week (Mon 12 – Fri 16 Oct 2026)."""
    _seed_snapshot(session, datetime(2026, 10, 9, 20, 0, tzinfo=UTC), "10000", "100")
    _seed_snapshot(session, datetime(2026, 10, 13, 20, 0, tzinfo=UTC), "9950", "99.8")
    _seed_snapshot(session, datetime(2026, 10, 16, 20, 0, tzinfo=UTC), "10149", "100.73")
    # Winner: +1.5R (risk (100-97)*10=30, pnl 45).
    winner = _seed_decision(
        session,
        _ny(date(2026, 10, 13)),
        symbol="AAPL",
        final_action="enter",
        stop=97.0,
    )
    _seed_position(
        session,
        winner,
        opened=_ny(date(2026, 10, 13), time(11, 0)),
        closed=_ny(date(2026, 10, 15), time(15, 0)),
        pnl="45.00",
    )
    # Loser: -1R.
    loser = _seed_decision(
        session,
        _ny(date(2026, 10, 14)),
        symbol="XOM",
        final_action="enter",
        stop=97.0,
    )
    _seed_position(
        session,
        loser,
        opened=_ny(date(2026, 10, 14), time(11, 0)),
        closed=_ny(date(2026, 10, 15), time(15, 30)),
        pnl="-30.00",
        exit_reason="stop",
    )
    # Model skip with a 5-session outcome 3% up (> 2% band → "up").
    skip = _seed_decision(
        session,
        _ny(date(2026, 10, 13), time(11, 46)),
        symbol="AMZN",
        final_action="skip",
        price=200.0,
    )
    skip_outcome = session.execute(
        select(Outcome).where(Outcome.decision_id == skip.id)
    ).scalar_one()
    skip_outcome.price_5sessions = Decimal("206.00")
    skip_outcome.price_5sessions_at = _naive(
        _ny(date(2026, 10, 20), time(11, 46))
    )
    # Model skip still waiting for its outcome.
    _seed_decision(
        session,
        _ny(date(2026, 10, 15), time(14, 46)),
        symbol="MSFT",
        final_action="skip",
    )
    # A rules no_call must never appear among skips or verdicts.
    _seed_decision(
        session,
        _ny(date(2026, 10, 14), time(12, 46)),
        symbol="GOOGL",
        final_action="no_call",
        no_call_reason="rules",
    )
    # One confirmed and one draft verdict inside the window.
    session.add(
        Verdict(
            decision_id=winner.id,
            verdict="good_call",
            reason="Target reached.",
            source="marc",
            created_at=_naive(datetime(2026, 10, 16, 10, 0, tzinfo=UTC)),
            updated_at=_naive(datetime(2026, 10, 16, 10, 0, tzinfo=UTC)),
        )
    )
    session.add(
        Verdict(
            decision_id=loser.id,
            verdict="bad_call",
            reason="Stopped out.",
            source="draft",
            created_at=_naive(datetime(2026, 10, 16, 10, 0, tzinfo=UTC)),
            updated_at=_naive(datetime(2026, 10, 16, 10, 0, tzinfo=UTC)),
        )
    )


async def test_weekly_facts_computed_from_fixture(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _seed_week(session)
        session.commit()
        facts = weekly_facts(
            session,
            config=_config(),
            clock=FakeClock(SATURDAY),
            calendar=FakeCalendarSource(),
            monday=MONDAY,
        )

    assert facts["period"]["week"] == 42
    assert facts["period"]["start"] == "2026-10-12"
    assert facts["period"]["end"] == "2026-10-16"
    # 10149/10000 - 1 = +1.49%; SPY 100→100.73 = +0.73% → +0.76 pts.
    assert facts["return_pct"] == pytest.approx(1.49, abs=0.01)
    assert facts["spy_return_pct"] == pytest.approx(0.73, abs=0.01)
    assert facts["vs_spy_pts"] == pytest.approx(0.76, abs=0.01)
    assert facts["equity_start"] == pytest.approx(10000)
    assert facts["equity_end"] == pytest.approx(10149)
    # Peak 10000 → trough 9950 = -0.5% close-to-close.
    assert facts["max_drawdown_pct"] == pytest.approx(-0.5, abs=0.01)
    assert facts["trades_closed"] == 2
    assert facts["won"] == 1
    assert facts["lost"] == 1
    # +1.5R and -1.0R → average +0.25R.
    assert facts["avg_r"] == pytest.approx(0.25, abs=0.01)
    assert facts["best_trade"]["symbol"] == "AAPL"
    assert facts["worst_trade"]["symbol"] == "XOM"
    # 4 model decisions × $0.001 (the rules no_call made no model call).
    assert facts["model_cost_usd"] == pytest.approx(0.004, abs=0.0001)
    assert facts["model_calls"] == 4
    # Two model skips (the rules no_call is excluded): one up, one pending.
    assert facts["skips"]["total"] == 2
    assert facts["skips"]["up"] == 1
    assert facts["skips"]["flat"] == 0
    assert facts["skips"]["down"] == 0
    assert facts["skips"]["pending"] == 1
    assert facts["skips"]["biggest_miss"]["symbol"] == "AMZN"
    assert facts["skips"]["biggest_miss"]["pct"] == pytest.approx(3.0, abs=0.1)
    # Only the confirmed (marc) verdict counts.
    assert facts["verdicts"]["total"] == 1
    assert facts["verdicts"]["good_call"] == 1
    assert facts["verdicts"]["bad_call"] == 0


# ----------------------------------------------------------------------
# number check


def test_number_check_pass() -> None:
    facts = {
        "return_pct": 1.49,
        "spy_return_pct": 0.73,
        "trades": [{"symbol": "AAPL", "r": 1.6}, {"symbol": "XOM", "r": -1.0}],
    }
    text = "Return +1.49% vs SPY +0.73%; best AAPL +1.6R, worst XOM -1R."
    assert bad_numbers(text, facts) == []


def test_number_check_fail() -> None:
    facts = {"return_pct": 1.49, "trades_closed": 3}
    assert bad_numbers("Return +1.49% over 4 trades.", facts) == ["4"]


def test_number_check_ignores_clock_times() -> None:
    assert bad_numbers("Written at 10:00, +1.49% return.", {"return_pct": 1.49}) == []


# ----------------------------------------------------------------------
# fallbacks: every reason → plain template + logged event


async def test_fallback_without_client(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _seed_week(session)
        session.commit()
    service = _service(session_factory, FakeClock(SATURDAY), client=None)
    review = await service.write_weekly_for(SATURDAY)

    assert review is not None
    assert review.source == "template"
    assert review.reason == "no OpenAI key"
    assert "Week 42" in review.text
    events = _fallback_events(session_factory)
    assert len(events) == 1
    assert (events[0].details or {})["reason"] == "no OpenAI key"


async def test_fallback_on_call_failure(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _seed_week(session)
        session.commit()
    service = _service(
        session_factory,
        FakeClock(SATURDAY),
        client=FakeReviewClient(error="boom"),
    )
    review = await service.write_weekly_for(SATURDAY)

    assert review is not None
    assert review.source == "template"
    assert review.reason == "model call failed"
    assert (_fallback_events(session_factory)[0].details or {})["reason"] == (
        "model call failed"
    )


async def test_fallback_on_number_check(
    session_factory: sessionmaker[Session],
) -> None:
    """A number that matches no fact → the plain template is used."""
    with session_factory() as session:
        _seed_week(session)
        session.commit()
    invented: dict[str, object] = {
        "summary": "Return +9.99%, an invented figure.",
        "observations": [],
        "suggestion": None,
    }
    service = _service(
        session_factory,
        FakeClock(SATURDAY),
        client=FakeReviewClient(handler=lambda _s, _u: json.dumps(invented)),
    )
    review = await service.write_weekly_for(SATURDAY)

    assert review is not None
    assert review.source == "template"
    assert review.reason == "number check failed"
    assert "+9.99%" not in review.text
    assert (_fallback_events(session_factory)[0].details or {})["reason"] == (
        "number check failed"
    )


async def test_fallback_on_invalid_output(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _seed_week(session)
        session.commit()
    service = _service(
        session_factory,
        FakeClock(SATURDAY),
        client=FakeReviewClient(handler=lambda _s, _u: "not json"),
    )
    review = await service.write_weekly_for(SATURDAY)

    assert review is not None
    assert review.source == "template"
    assert review.reason == "invalid model output"


async def test_fallback_on_cost_cap(
    session_factory: sessionmaker[Session],
) -> None:
    """The independent $2 cap: reached spend → plain template, no call."""
    with session_factory() as session:
        _seed_week(session)
        # $2.50 already spent on reviews this month.
        session.add(
            Review(
                kind="briefing",
                period="2026-10-15",
                facts={},
                text="x",
                source="model",
                cost=Decimal("2.50"),
                due_at=_naive(datetime(2026, 10, 15, 13, 0, tzinfo=UTC)),
                created_at=_naive(datetime(2026, 10, 15, 13, 0, tzinfo=UTC)),
            )
        )
        session.commit()
    called = False

    def handler(_s: str, _u: str) -> str:
        nonlocal called
        called = True
        return "{}"

    service = _service(
        session_factory,
        FakeClock(SATURDAY),
        client=FakeReviewClient(handler=handler),
    )
    review = await service.write_weekly_for(SATURDAY)

    assert review is not None
    assert review.source == "template"
    assert review.reason == "cost cap reached"
    assert called is False
    assert (_fallback_events(session_factory)[0].details or {})["reason"] == (
        "cost cap reached"
    )


async def test_verdict_spend_counts_toward_the_cap(
    session_factory: sessionmaker[Session],
) -> None:
    """Verdict calls are review calls: their spend blocks the next call."""
    with session_factory() as session:
        _seed_week(session)
        decision = _seed_decision(session, _ny(date(2026, 10, 15)), symbol="MSFT")
        session.add(
            Verdict(
                decision_id=decision.id,
                verdict="good_call",
                reason="x",
                source="draft",
                draft_source="model",
                cost=Decimal("2.50"),
                created_at=_naive(datetime(2026, 10, 15, 13, 0, tzinfo=UTC)),
                updated_at=_naive(datetime(2026, 10, 15, 13, 0, tzinfo=UTC)),
            )
        )
        session.commit()
    called = False

    def handler(_s: str, _u: str) -> str:
        nonlocal called
        called = True
        return "{}"

    service = _service(
        session_factory,
        FakeClock(SATURDAY),
        client=FakeReviewClient(handler=handler),
    )
    review = await service.write_weekly_for(SATURDAY)

    assert review is not None
    assert review.source == "template"
    assert review.reason == "cost cap reached"
    assert called is False


async def test_model_text_passes_number_check(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _seed_week(session)
        session.commit()
    service = _service(
        session_factory, FakeClock(SATURDAY), client=FakeReviewClient()
    )
    review = await service.write_weekly_for(SATURDAY)

    assert review is not None
    assert review.kind == "weekly"
    assert review.period == "2026-W42"
    assert review.source == "model"
    assert review.reason is None
    assert review.model == "fake-review"
    assert review.prompt_version == "weekly.v1"
    assert review.cost is not None
    assert (review.content or {})["summary"] == review.text
    # A caught-up/scheduled write keeps the Saturday slot as due_at.
    assert review.due_at == _naive(SATURDAY)


# ----------------------------------------------------------------------
# scheduling and catch-up


async def test_missed_weekly_reviews_written_with_original_dates(
    session_factory: sessionmaker[Session],
) -> None:
    """App last ran Monday 5 Oct and restarts Wednesday 21 Oct: the
    Saturdays of 10 and 17 Oct were missed and are written now, each
    with its own original due date."""
    with session_factory() as session:
        session.add(
            Run(
                started_at=_naive(datetime(2026, 10, 5, 12, 0, tzinfo=UTC)),
                starting_equity=Decimal("10000"),
            )
        )
        session.commit()
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # Wednesday
    service = _service(session_factory, FakeClock(now))
    written = await service.catch_up()

    reviews = {r.period: r for r in _reviews(session_factory)}
    assert written["weekly"] == 2
    assert set(reviews) == {"2026-W41", "2026-W42"}
    assert reviews["2026-W41"].due_at == _naive(
        datetime(2026, 10, 10, 8, 0, tzinfo=UTC)
    )
    assert reviews["2026-W42"].due_at == _naive(SATURDAY)


async def test_catch_up_writes_todays_briefing_only(
    session_factory: sessionmaker[Session],
) -> None:
    """Wednesday restart: only today's briefing is written; Monday's and
    Tuesday's are older than their trading day and skipped."""
    with session_factory() as session:
        session.add(
            Run(
                started_at=_naive(datetime(2026, 10, 19, 12, 0, tzinfo=UTC)),
                starting_equity=Decimal("10000"),
            )
        )
        session.commit()
    # Wednesday 21 Oct 16:00 NY → past the 09:00 NY briefing slot.
    now = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    service = _service(session_factory, FakeClock(now))
    written = await service.catch_up()

    briefings = [
        r for r in _reviews(session_factory) if r.kind == "briefing"
    ]
    assert written["briefing"] == 1
    assert [b.period for b in briefings] == ["2026-10-21"]
    assert briefings[0].due_at == _naive(
        datetime(2026, 10, 21, 13, 0, tzinfo=UTC)
    )  # 09:00 EDT


async def test_briefing_before_slot_not_written(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(
            Run(
                started_at=_naive(datetime(2026, 10, 19, 12, 0, tzinfo=UTC)),
                starting_equity=Decimal("10000"),
            )
        )
        session.commit()
    now = datetime(2026, 10, 21, 12, 30, tzinfo=UTC)  # 08:30 NY — too early
    service = _service(session_factory, FakeClock(now))
    written = await service.catch_up()
    assert written["briefing"] == 0
    assert not any(
        r.kind == "briefing" for r in _reviews(session_factory)
    )


async def test_briefing_can_be_disabled(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(
            Run(
                started_at=_naive(datetime(2026, 10, 19, 12, 0, tzinfo=UTC)),
                starting_equity=Decimal("10000"),
            )
        )
        session.commit()
    now = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    service = _service(
        session_factory,
        FakeClock(now),
        config=_config(briefing_enabled=False),
    )
    written = await service.catch_up()
    assert written["briefing"] == 0
    assert not any(
        r.kind == "briefing" for r in _reviews(session_factory)
    )


# ----------------------------------------------------------------------
# draft verdicts


async def test_verdict_written_only_after_five_sessions(
    session_factory: sessionmaker[Session],
) -> None:
    """No draft while the 5-session outcome is missing; once it lands,
    one draft verdict is written for the model skip."""
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    with session_factory() as session:
        skip = _seed_decision(session, _ny(date(2026, 10, 15)), symbol="AMZN",
                              final_action="skip", price=200.0)
        session.commit()
        skip_id = skip.id

    service = _service(session_factory, FakeClock(now))
    assert await service.write_due_verdicts() == 0
    assert _verdicts(session_factory) == []

    with session_factory() as session:
        outcome = session.execute(
            select(Outcome).where(Outcome.decision_id == skip_id)
        ).scalar_one()
        outcome.price_5sessions = Decimal("206.00")
        outcome.price_5sessions_at = _naive(_ny(date(2026, 10, 22)))
        session.commit()

    assert await service.write_due_verdicts() == 1
    verdicts = _verdicts(session_factory)
    assert len(verdicts) == 1
    assert verdicts[0].decision_id == skip_id
    assert verdicts[0].source == "draft"
    # +3% is outside the ±2% flat band → the template calls it a bad call.
    assert verdicts[0].verdict == "bad_call"
    # Idempotent: no second row on the next scan.
    assert await service.write_due_verdicts() == 0
    assert len(_verdicts(session_factory)) == 1


async def test_no_verdict_for_rules_no_call(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    with session_factory() as session:
        decision = _seed_decision(
            session,
            _ny(date(2026, 10, 15)),
            symbol="GOOGL",
            final_action="no_call",
            no_call_reason="rules",
        )
        outcome = session.execute(
            select(Outcome).where(Outcome.decision_id == decision.id)
        ).scalar_one()
        outcome.price_5sessions = Decimal("206.00")
        session.commit()
        assert not verdict_eligible(decision)

    service = _service(session_factory, FakeClock(now))
    assert await service.write_due_verdicts() == 0
    assert _verdicts(session_factory) == []


async def test_verdict_for_enter_uses_trade_r(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    with session_factory() as session:
        entry = _seed_decision(
            session,
            _ny(date(2026, 10, 15)),
            symbol="AAPL",
            final_action="enter",
            stop=97.0,
        )
        outcome = session.execute(
            select(Outcome).where(Outcome.decision_id == entry.id)
        ).scalar_one()
        outcome.price_5sessions = Decimal("104.00")
        outcome.price_5sessions_at = _naive(_ny(date(2026, 10, 22)))
        _seed_position(
            session,
            entry,
            opened=_ny(date(2026, 10, 15), time(11, 0)),
            closed=_ny(date(2026, 10, 20), time(15, 0)),
            pnl="45.00",
            exit_reason="target",
        )
        session.commit()

    service = _service(session_factory, FakeClock(now))
    assert await service.write_due_verdicts() == 1
    verdict = _verdicts(session_factory)[0]
    assert verdict.verdict == "good_call"  # +1.5R target hit
    assert verdict.reason is not None and "target" in verdict.reason


# ---------------------------------------------------------------------------
# API


def _seed_api_review_and_verdict() -> tuple[int, int, int]:
    """One weekly review + one decision with a draft verdict, in the app's
    database. Returns (review_id, decision_id, verdict_id)."""
    from app.db.base import get_session_maker

    with get_session_maker()() as session:
        review = Review(
            kind="weekly",
            period="2026-W42",
            facts={
                "period": {
                    "iso_year": 2026,
                    "week": 42,
                    "start": "2026-10-12",
                    "end": "2026-10-16",
                    "label": "12 – 16 Oct",
                },
                "return_pct": 0.5,
            },
            text="Week 42: return +0.5%.",
            content={"summary": "Week 42: return +0.5%.", "observations": []},
            source="template",
            reason="no OpenAI key",
            due_at=datetime(2026, 10, 17, 8, 0),
            created_at=datetime(2026, 10, 17, 8, 0),
        )
        session.add(review)
        decision = _seed_decision(
            session,
            _ny(date(2026, 10, 15)),
            symbol="MSFT",
            final_action="enter",
            price=420.0,
        )
        verdict = Verdict(
            decision_id=decision.id,
            verdict="good_call",
            reason="Up after 5 sessions.",
            source="draft",
            draft_source="model",
            created_at=datetime(2026, 10, 22, 12, 0),
            updated_at=datetime(2026, 10, 22, 12, 0),
        )
        session.add(verdict)
        session.commit()
        return review.id, decision.id, verdict.id


def test_reviews_endpoints(client: TestClient) -> None:
    review_id, _, _ = _seed_api_review_and_verdict()

    response = client.get("/api/reviews?kind=weekly")
    assert response.status_code == 200
    data = response.json()
    assert [i["id"] for i in data["items"]] == [review_id]
    assert data["items"][0]["label"] == "Week 42"
    assert data["items"][0]["return_pct"] == 0.5
    assert data["next_weekly_due"] is not None
    assert "Weekly review" in data["schedule"]

    detail = client.get(f"/api/reviews/{review_id}").json()
    assert detail["period"] == "2026-W42"
    assert detail["facts"]["return_pct"] == 0.5
    assert detail["reason"] == "no OpenAI key"

    assert client.get("/api/reviews?kind=nope").status_code == 400
    assert client.get("/api/reviews/999").status_code == 404


def test_verdicts_endpoints(client: TestClient) -> None:
    _, decision_id, verdict_id = _seed_api_review_and_verdict()

    data = client.get("/api/verdicts?status=draft").json()
    assert [i["id"] for i in data["items"]] == [verdict_id]
    assert data["items"][0]["symbol"] == "MSFT"
    assert client.get("/api/verdicts?status=confirmed").json()["items"] == []

    # Confirming a different value still makes it Marc's verdict.
    response = client.post(f"/api/verdicts/{verdict_id}", json={"verdict": "bad_call"})
    assert response.status_code == 200
    assert response.json() == {"id": verdict_id, "verdict": "bad_call", "source": "marc"}
    assert client.get("/api/verdicts?status=draft").json()["items"] == []
    confirmed = client.get("/api/verdicts?status=confirmed").json()["items"]
    assert confirmed[0]["verdict"] == "bad_call"

    # The decision drawer shows the confirmed verdict.
    detail = client.get(f"/api/decisions/{decision_id}").json()
    assert detail["verdict"]["verdict"] == "bad_call"
    assert detail["verdict"]["source"] == "marc"
    assert detail["verdict_eligible"] is True

    assert (
        client.post(f"/api/verdicts/{verdict_id}", json={"verdict": "meh"}).status_code
        == 422
    )
    assert client.post("/api/verdicts/999", json={"verdict": "good_call"}).status_code == 404


# ---------------------------------------------------------------------------
# dashboard: briefing card + model call counter (docs/SPEC.md, GUI)


def _clear_briefings() -> None:
    """Drop the briefings the app wrote at startup so a test can control
    what the dashboard card shows."""
    from app.db.base import get_session_maker

    with get_session_maker()() as session:
        session.execute(delete(Review).where(Review.kind == "briefing"))
        session.commit()


def _seed_briefing(
    *,
    period: str = "2026-10-14",
    source: str = "model",
    reason: str | None = None,
) -> int:
    """One written briefing in the app's database; returns its id."""
    from app.db.base import get_session_maker

    with get_session_maker()() as session:
        review = Review(
            kind="briefing",
            period=period,
            facts={
                "positions": [
                    {
                        "symbol": "JPM",
                        "qty": 8,
                        "price": 232.8,
                        "stop": 226.1,
                        "stop_distance_pct": 2.9,
                    }
                ],
                "earnings_today": [],
                "earnings_tomorrow": [
                    {"symbol": "GOOGL", "time_of_day": "before_open"}
                ],
                "regime": "Up · normal vol",
                "yesterday": {"return_pct": 0.18, "return_usd": 18.4},
                "banners": [],
            },
            text="One position open: JPM.",
            content={"text": "One position open: JPM."},
            source=source,
            reason=reason,
            model="gpt-4o-mini" if source == "model" else None,
            prompt_version="briefing.v1",
            tokens=900 if source == "model" else None,
            cost=Decimal("0.0004") if source == "model" else None,
            due_at=datetime(2026, 10, 14, 13, 0),
            created_at=datetime(2026, 10, 14, 13, 0),
        )
        session.add(review)
        session.commit()
        return review.id


def test_dashboard_briefing_card(client: TestClient) -> None:
    """The card shows the latest briefing with the fact chips it used,
    its provenance and when the next one is written."""
    _clear_briefings()
    _seed_briefing()
    briefing = client.get("/api/dashboard").json()["briefing"]
    assert briefing["enabled"] is True
    assert briefing["next_at"] is not None
    card = briefing["card"]
    # The frozen clock sits on Thu 15 Oct: yesterday's briefing shows
    # with its date, marked as not today's.
    assert card["label"] == "Wed 14 Oct"
    assert card["today"] is False
    assert card["text"] == "One position open: JPM."
    assert card["source"] == "model"
    assert card["model"] == "gpt-4o-mini"
    assert card["prompt_version"] == "briefing.v1"
    assert float(card["cost"]) == pytest.approx(0.0004)
    texts = [c["text"] for c in card["chips"]]
    assert texts == [
        "1 position",
        "yesterday +0.18% · +$18.40",
        "market up · normal vol",
        "earnings GOOGL tomorrow",
        "no banners",
    ]
    # Its cost feeds the model-call tile's review spend for October.
    stats = client.get("/api/dashboard").json()["stats"]
    assert float(stats["reviews_cost_month"]) == pytest.approx(0.0004)
    assert stats["reviews_cost_cap"] == "2"


def test_dashboard_briefing_card_today(client: TestClient) -> None:
    """The app writes today's briefing at startup; the card marks it as
    today's so it can show the write time instead of a date."""
    card = client.get("/api/dashboard").json()["briefing"]["card"]
    assert card is not None
    assert card["today"] is True
    assert card["label"] == "Thu 15 Oct"
    assert card["time"] == "15:00"  # 09:00 New York, Amsterdam time
    assert len(card["chips"]) == 5


def test_dashboard_briefing_card_template(client: TestClient) -> None:
    """A plain-template briefing carries the fallback reason and no
    model or cost."""
    _clear_briefings()
    _seed_briefing(source="template", reason="number check failed")
    card = client.get("/api/dashboard").json()["briefing"]["card"]
    assert card["source"] == "template"
    assert card["reason"] == "number check failed"
    assert card["model"] is None
    assert card["cost"] is None
    assert card["prompt_version"] == "briefing.v1"


def test_dashboard_briefing_disabled(client: TestClient) -> None:
    """With briefings turned off the card still renders, saying so,
    with no next write time."""
    from app.config import get_config

    get_config()._active.setdefault("reviews", {})["briefing_enabled"] = False
    briefing = client.get("/api/dashboard").json()["briefing"]
    assert briefing["enabled"] is False
    assert briefing["next_at"] is None


def test_dashboard_call_counter(client: TestClient) -> None:
    """The Model calls today tile: real calls against the day's maximum,
    with today's and this month's decision cost."""
    from app.db.base import get_session_maker

    stats = client.get("/api/dashboard").json()["stats"]
    assert stats["calls_per_cycle"] == 10
    assert stats["calls_max"] == 10 * stats["cycles_today"]
    before_calls = stats["calls_today"]
    before_today = float(stats["calls_cost_today"])
    before_month = float(stats["calls_cost_month"])

    with get_session_maker()() as session:
        # A real model call and a rules no_call (not counted, no cost).
        _seed_decision(session, _ny(date(2026, 10, 15), time(9, 46)))
        _seed_decision(
            session,
            _ny(date(2026, 10, 15), time(9, 46)),
            symbol="AMZN",
            final_action="no_call",
            no_call_reason="rules",
        )
        session.commit()

    stats = client.get("/api/dashboard").json()["stats"]
    assert stats["calls_today"] == before_calls + 1
    assert float(stats["calls_cost_today"]) == pytest.approx(before_today + 0.001)
    assert float(stats["calls_cost_month"]) == pytest.approx(before_month + 0.001)
