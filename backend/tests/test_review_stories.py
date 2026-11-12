"""Review stories (item 079, docs/SPEC.md — Reviews → Stories).

weekly.v3 and briefing.v3 write one to three plain paragraphs; code
checks the format, the number check still applies, the glossary rides
in the facts, and a failed check falls back to a prose template. v1/v2
keep their own shapes and word caps.
"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from app.config import AppConfig
from app.db.models import Cycle, Run
from app.market.clock import FakeClock
from app.reviews.client import SCHEMAS, FakeReviewClient
from app.reviews.contracts import parse_output
from app.reviews.facts import briefing_facts
from app.reviews.glossary import glossary_for
from app.reviews.numbercheck import format_problems
from app.reviews.service import ReviewService
from app.reviews.templates import (
    briefing_story_plain,
    plain_content,
    weekly_story_plain,
)

from tests.test_reviews import (
    SATURDAY,
    _config,
    _fallback_events,
    _ny,
    _seed_week,
    _service,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def session_factory(tmp_path):
    """Same shape as test_reviews': rows stay readable after commit."""
    from app.db.base import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


# ----------------------------------------------------------------------
# contracts and prompt registration


def test_v3_contracts_and_schemas_registered() -> None:
    assert "weekly.v3" in ReviewService._REVIEW_CONTRACTS["weekly"]
    assert "briefing.v3" in ReviewService._REVIEW_CONTRACTS["briefing"]
    assert "weekly.v3" in SCHEMAS and "briefing.v3" in SCHEMAS
    # The prompt files are in the manifest; loading verifies the hash.
    from app.decide.prompt import cached_prompt

    assert cached_prompt("weekly.v3").version == "weekly.v3"
    assert cached_prompt("briefing.v3").version == "briefing.v3"


def test_parse_output_per_version() -> None:
    # weekly.v3: story + the v2 suggestion shape.
    ok = parse_output(
        "weekly.v3",
        {
            "story": "One paragraph.",
            "suggestion": {"text": "Tighten stops.", "evidence": "avg -1R"},
        },
    )
    assert ok is not None and ok["story"] == "One paragraph."
    # A list-shaped or summary-shaped answer is not a v3 answer.
    assert parse_output("weekly.v3", {"summary": "x", "observations": []}) is None
    assert (
        parse_output("weekly.v3", {"story": "x", "suggestion": "a string"})
        is None
    )
    # weekly.v2 still wants summary + observations; its suggestion may be
    # the v2 object.
    assert (
        parse_output(
            "weekly.v2",
            {"summary": "s", "observations": ["o"], "suggestion": None},
        )
        is not None
    )
    assert parse_output("weekly.v1", {"summary": "s", "observations": []}) is not None
    # briefing.v3 shares the {"text"} shape with v1/v2.
    assert parse_output("briefing.v3", {"text": "t"}) == {"text": "t"}


# ----------------------------------------------------------------------
# word caps and the format check


def _words(n: int) -> str:
    return " ".join(["word"] * n)


def test_v2_briefing_keeps_fixed_80_word_limit() -> None:
    config = _config()
    assert (
        ReviewService._validate(
            "briefing", "briefing.v2", {"text": _words(150)}, config
        )
        is None
    )
    # 79 words still pass.
    assert (
        ReviewService._validate(
            "briefing", "briefing.v2", {"text": _words(79)}, config
        )
        is not None
    )


def test_v3_briefing_uses_config_cap_via_format_check() -> None:
    config = _config()
    facts = {"limits": {"briefing_words": 220}}
    # 150 words: over the old 80 but inside the v3 cap — the shape check
    # passes and the format check is what judges it.
    content = ReviewService._validate(
        "briefing", "briefing.v3", {"text": _words(150)}, config
    )
    assert content is not None
    screened, reason, _ = ReviewService._screen_content(
        "briefing", "briefing.v3", content, facts, config
    )
    assert screened is not None and reason is None
    # 221 words: the format check fails with its own reason.
    content = ReviewService._validate(
        "briefing", "briefing.v3", {"text": _words(221)}, config
    )
    screened, reason, detail = ReviewService._screen_content(
        "briefing", "briefing.v3", content, facts, config
    )
    assert screened is None and reason == "format check failed"
    assert "words" in (detail or "")


def test_weekly_v3_story_cap_is_separate_from_summary_cap() -> None:
    config = _config()
    facts = {"limits": {"weekly_story_words": 350}}
    # A v2 summary over 120 words fails at the shape check.
    assert (
        ReviewService._validate(
            "weekly",
            "weekly.v2",
            {"summary": _words(150), "observations": [], "suggestion": None},
            config,
        )
        is None
    )
    # A v3 story of 340 words passes the shape check and the format check.
    content = ReviewService._validate(
        "weekly",
        "weekly.v3",
        {"story": _words(340), "suggestion": None},
        config,
    )
    screened, reason, _ = ReviewService._screen_content(
        "weekly", "weekly.v3", content, facts, config
    )
    assert screened is not None and reason is None
    # 351 words fails the format check, not the shape check.
    content = ReviewService._validate(
        "weekly",
        "weekly.v3",
        {"story": _words(351), "suggestion": None},
        config,
    )
    screened, reason, _ = ReviewService._screen_content(
        "weekly", "weekly.v3", content, facts, config
    )
    assert screened is None and reason == "format check failed"


def test_format_problems_rules() -> None:
    good = "First paragraph here.\n\nSecond paragraph here."
    assert format_problems(good, 220) == []
    # One paragraph is fine too.
    assert format_problems("Only one.", 220) == []
    assert format_problems("a\n\nb\n\nc\n\nd", 220)  # 4 paragraphs
    assert format_problems("- a point\n\n- another", 220)  # list lines
    assert format_problems("# Heading\n\ntext", 220)  # heading
    assert format_problems(_words(221), 220)  # over the cap
    assert format_problems("1. numbered\n\ntext", 220)  # numbered list line


async def test_weekly_v3_format_failure_falls_back_to_prose(
    session_factory,
) -> None:
    """A story-shaped answer that breaks the format rules gets the prose
    template labelled "format check failed" — not "invalid model output"."""
    with session_factory() as session:
        _seed_week(session)
        session.commit()
    bad_story = {"story": "a\n\nb\n\nc\n\nd", "suggestion": None}
    service = _service(
        session_factory,
        FakeClock(SATURDAY),
        client=FakeReviewClient(handler=lambda _s, _u: json.dumps(bad_story)),
    )
    review = await service.write_weekly_for(SATURDAY)

    assert review is not None
    assert review.source == "template"
    assert review.reason == "format check failed"
    assert review.prompt_version == "weekly.v3"
    assert "Week 42" in review.text
    assert (review.content or {}).get("story") == review.text
    assert (_fallback_events(session_factory)[0].details or {})["reason"] == (
        "format check failed"
    )


async def test_list_shaped_answer_falls_back_under_v3(
    session_factory,
) -> None:
    """The old {summary, observations} shape is not a v3 answer: the
    fallback is the story template, labelled invalid model output."""
    with session_factory() as session:
        _seed_week(session)
        session.commit()
    legacy = {"summary": "A summary.", "observations": [], "suggestion": None}
    service = _service(
        session_factory,
        FakeClock(SATURDAY),
        client=FakeReviewClient(handler=lambda _s, _u: json.dumps(legacy)),
    )
    review = await service.write_weekly_for(SATURDAY)

    assert review is not None
    assert review.source == "template"
    assert review.reason == "invalid model output"
    assert "story" in (review.content or {})


async def test_weekly_v3_suggestion_gate_unchanged(session_factory) -> None:
    """The v2 suggestion rules apply to v3: kept when the sample allows
    it, dropped when it doesn't — the story itself still lands."""
    story = "A quiet week.\n\nNo trades closed.\n\nThe sample is still small."
    suggestion = {"text": "Wait for more trades.", "evidence": "few closed"}
    answer = {"story": story, "suggestion": suggestion}

    with session_factory() as session:
        _seed_week(session)
        session.commit()
    # The seeded run has 2 closed trades < 10 → the suggestion drops.
    service = _service(
        session_factory,
        FakeClock(SATURDAY),
        client=FakeReviewClient(handler=lambda _s, _u: json.dumps(answer)),
    )
    review = await service.write_weekly_for(SATURDAY)
    assert review is not None and review.source == "model"
    assert (review.content or {}).get("suggestion") is None

    # With the gate opened the same answer keeps its suggestion.
    config = _config(min_closed_trades_for_suggestion=0)
    service = _service(
        session_factory,
        FakeClock(SATURDAY),
        client=FakeReviewClient(handler=lambda _s, _u: json.dumps(answer)),
        config=config,
    )
    # A second period avoids the (kind, period) unique key.
    review2 = await service.write_weekly_for(SATURDAY + timedelta(days=7))
    assert review2 is not None and review2.source == "model"
    kept = (review2.content or {}).get("suggestion")
    assert isinstance(kept, dict) and kept["text"] == "Wait for more trades."


async def test_briefing_v3_format_failure_falls_back(session_factory) -> None:
    day = date(2026, 10, 22)
    with session_factory() as session:
        session.add(
            Run(
                started_at=datetime(2026, 10, 19, 12, 0).replace(tzinfo=None),
                starting_equity=Decimal("10000"),
            )
        )
        session.commit()
    bad = {"text": "one\n\ntwo\n\nthree\n\nfour"}
    service = _service(
        session_factory,
        FakeClock(_ny(day)),
        client=FakeReviewClient(handler=lambda _s, _u: json.dumps(bad)),
    )
    review = await service.write_briefing_for(day)
    assert review is not None
    assert review.source == "template"
    assert review.reason == "format check failed"
    assert review.prompt_version == "briefing.v3"
    # The prose template is still paragraph-structured prose.
    assert "no positions are open" in review.text


# ----------------------------------------------------------------------
# glossary


def _glossary_config(**risk: object) -> AppConfig:
    config = AppConfig()
    config.yaml.setdefault("risk", {}).update(risk)
    return config


def test_glossary_briefing_terms_and_thresholds() -> None:
    facts = {
        "kind": "briefing",
        "positions": [{"symbol": "JPM", "stop": 226.1}],
        "regime": "Up · normal vol",
        "vix": {"close": 17.4, "percentile_1y": 42.0},
        "next_release": {"label": "CPI"},
        "earnings_today": [{"symbol": "JPM"}],
        "earnings_tomorrow": [],
    }
    glossary = glossary_for(facts, _glossary_config(earnings_blackout_days=5))
    assert set(glossary) == {
        "stop",
        "regime",
        "VIX",
        "percentile",
        "release",
        "earnings blackout",
    }
    # The threshold comes from settings, not a hard-coded number.
    assert "5 trading days" in glossary["earnings blackout"]
    assert "8%" not in glossary.get("drawdown", "")  # no drawdown fact here


def test_glossary_weekly_terms() -> None:
    facts = {
        "kind": "weekly",
        "trades_closed": 2,
        "won": 1,
        "lost": 1,
        "avg_r": 0.5,
        "return_usd": 50.0,
        "max_drawdown_pct": -0.5,
        "trades": [{"symbol": "A", "exit": "target", "r": 1.5}],
        "sample": {"closed_trades": {"run": 2}},
        "verdicts": {"total": 1},
    }
    glossary = glossary_for(facts, _config())
    for term in ("R", "target", "drawdown", "adjusted P&L", "win rate",
                 "expectancy", "sample", "verdicts"):
        assert term in glossary, term
    assert "stop" not in glossary  # no stop exit and no position stop
    # The kill-switch threshold reads risk.drawdown_kill_pct.
    config = _glossary_config(drawdown_kill_pct=12)
    assert "−12%" in glossary_for(facts, config)["drawdown"]


def test_glossary_only_terms_with_facts() -> None:
    assert glossary_for({"kind": "briefing", "positions": []}, _config()) == {}
    assert glossary_for({"kind": "weekly"}, _config()) == {}


def test_briefing_facts_carry_glossary_and_limits(
    session_factory,
) -> None:
    """The facts the model sees include the glossary and the v3 limits;
    a recorded VIX cycle surfaces the VIX terms."""
    from app.data.fake import FakeCalendarSource

    with session_factory() as session:
        session.add(
            Run(
                started_at=datetime(2026, 10, 19, 12, 0).replace(tzinfo=None),
                starting_equity=Decimal("10000"),
            )
        )
        session.add(
            Cycle(
                kind="cycle",
                scheduled_time=datetime(2026, 10, 20, 14, 30).replace(tzinfo=None),
                started_at=datetime(2026, 10, 20, 14, 30).replace(tzinfo=None),
                finished_at=datetime(2026, 10, 20, 14, 35).replace(tzinfo=None),
                status="done",
                vix_close=Decimal("17.40"),
                vix_date=date(2026, 10, 19),
                vix_percentile_1y=Decimal("42.00"),
            )
        )
        session.commit()
        facts = briefing_facts(
            session,
            config=_config(),
            clock=FakeClock(_ny(date(2026, 10, 22))),
            calendar=FakeCalendarSource(),
            market_data=None,
            signals=None,
            state=None,
            day=date(2026, 10, 22),
        )
    assert facts["vix"]["close"] == pytest.approx(17.4)
    assert facts["limits"]["briefing_words"] == 220
    assert facts["limits"]["earnings_blackout_days"] == 3
    glossary = facts["glossary"]
    assert "VIX" in glossary and "percentile" in glossary
    assert "regime" not in glossary  # regime is "unavailable" → no fact
    assert facts["next_release"] is None  # no release service passed


# ----------------------------------------------------------------------
# prose templates


def test_weekly_story_template_empty_facts() -> None:
    """Every fact group empty still produces valid three-paragraph prose."""
    content = weekly_story_plain({})
    story = content["story"]
    assert content["suggestion"] is None
    assert len([p for p in story.split("\n\n") if p.strip()]) == 3
    assert format_problems(story, 350) == []
    assert "Week" not in story  # no period facts → generic wording


def test_weekly_story_template_uses_facts() -> None:
    facts = {
        "period": {"week": 42, "label": "12 – 16 Oct"},
        "return_pct": 1.49,
        "return_usd": 149.0,
        "spy_return_pct": 0.73,
        "vs_spy_pts": 0.76,
        "max_drawdown_pct": -0.49,
        "trades_closed": 2,
        "won": 1,
        "lost": 1,
        "avg_r": 0.25,
        "best_trade": {"symbol": "AAPL", "r": 1.5, "exit": "target"},
        "worst_trade": {"symbol": "XOM", "r": -1.0, "exit": "stop"},
        "skips": {"total": 2, "with_outcome": 1, "up": 1, "flat": 0,
                  "down": 0, "biggest_miss": None},
        "limits": {"verdict_after_sessions": 5},
        "verdicts": {"total": 1, "good_call": 1, "bad_call": 0, "unclear": 0},
        "sample": {"closed_trades": {"run": 2},
                   "model_skips_with_outcome": {"run": 1},
                   "confirmed_verdicts": {"run": 1}},
    }
    story = weekly_story_plain(facts)["story"]
    assert "Week 42" in story
    assert format_problems(story, 350) == []
    # Every number in the template comes from the facts.
    from app.reviews.numbercheck import bad_numbers

    assert bad_numbers(story, facts) == []


def test_briefing_story_template_paragraphs() -> None:
    facts = {
        "positions": [{"symbol": "JPM", "qty": 8, "price": 232.8,
                       "stop": 226.1, "stop_distance_pct": 2.9}],
        "earnings_today": [{"symbol": "JPM"}],
        "earnings_tomorrow": [{"symbol": "XOM"}],
        "regime": "Up · normal vol",
        "vix": {"close": 17.4, "percentile_1y": 42.0, "stale": False},
        "next_release": {"label": "CPI", "date": "2026-10-16",
                         "time": "08:30"},
        "yesterday": {"return_pct": 0.18, "return_usd": 18.4,
                      "spy_return_pct": 0.42},
        "banners": [{"kind": "earnings_down"}],
    }
    content = briefing_story_plain(facts)
    paragraphs = [p for p in content["text"].split("\n\n") if p.strip()]
    assert len(paragraphs) == 3
    assert format_problems(content["text"], 220) == []
    assert "JPM" in paragraphs[0] and "Yesterday" in paragraphs[0]
    assert "VIX" in paragraphs[1] and "CPI" in paragraphs[1]
    assert "Earnings" in paragraphs[2] and "earnings down" in paragraphs[2]
    from app.reviews.numbercheck import bad_numbers

    assert bad_numbers(content["text"], facts) == []


def test_plain_content_dispatches_on_version() -> None:
    facts = {"kind": "weekly", "period": {"week": 42, "label": "x"}}
    assert "story" in plain_content("weekly", facts, "weekly.v3")
    assert "summary" in plain_content("weekly", facts, "weekly.v2")
    # No version → the pre-story template, as before.
    assert "summary" in plain_content("weekly", facts)
    briefing = plain_content("briefing", {"kind": "briefing"}, "briefing.v3")
    assert "text" in briefing
