"""Item 074 — LLM inspector UX: reasons dictionary, attention levels,
explanations, top reasons and per-cycle buckets.

Every attention rule is tested on both sides of its threshold; the
levels are write-once (a later call never rewrites a stored one); every
reason code and change reason the code can write has an explanation.
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.db.base import get_session_maker
from app.db.models import Cycle, LlmCall
from app.inspector.attention import attention_for_call
from app.inspector.explain import (
    change_reason_and_detail,
    detail_for,
    explanation_items,
)
from app.llm.reasons import (
    CHANGE_REASONS,
    REASONS,
    change_reason_key,
)
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

APP = Path(__file__).resolve().parents[1] / "app"
DAY = date(2026, 10, 22)
T0 = datetime(2026, 10, 22, 14, 0)  # 10:00 New York


def _call(session: Session, **kw: Any) -> LlmCall:
    defaults: dict[str, Any] = {
        "sent_at": T0,
        "session_date": DAY,
        "source": "live",
        "kind": "decision",
        "model": "fake-decision",
        "prompt_version": "decision.v1",
        "final_action": "skip",
        "outcome": "warning",
        "reasons": [],
        "attention": "none",
    }
    call = LlmCall(**{**defaults, **kw})
    session.add(call)
    return call


def _cycle(session: Session, at: datetime) -> int:
    cycle = Cycle(
        kind="cycle", scheduled_time=at, started_at=at,
        finished_at=at, status="done",
    )
    session.add(cycle)
    session.flush()
    return int(cycle.id)


def _attention(session: Session, call: LlmCall, payload: Any = None) -> str:
    return attention_for_call(
        session, call, input_payload=payload if isinstance(payload, dict) else None
    )


class TestReasonsDictionary:
    """Step 1: one entry per reason code and per change reason."""

    EXPECTED_CODES = {
        "changed_by_code",
        "input_incomplete",
        "number_check_failed",
        "slow",
        "unsupported_claim",
        "unresolved_conflict",
        "confidence_outlier",
        "best_option_missing",
        "best_option_mismatch",
        "hold_below_breakeven",
        "timeout",
        "api_error",
        "invalid_output",
        "refusal",
    }

    def test_every_outcome_reason_has_an_entry(self) -> None:
        assert set(REASONS) == self.EXPECTED_CODES
        for text in REASONS.values():
            assert text.title and text.means and text.cause and text.fix

    def test_change_reasons_the_code_writes_have_entries(self) -> None:
        """Scan the writers of change reasons; every literal must
        resolve to a CHANGE_REASONS entry or a check-name pattern."""
        found: set[str] = set()
        # Only the modules that write change reasons: validation
        # (decide/output), the gates (risk/gates), order-time blocks
        # (engine) and shadow bookkeeping (shadows/runner).
        writers = [
            APP / "decide" / "output.py",
            APP / "risk" / "gates.py",
            APP / "engine" / "executor.py",
            APP / "engine" / "cycle.py",
            APP / "shadows" / "runner.py",
        ]
        for path in writers:
            src = path.read_text()
            for pat in (
                r'block\("([a-z_]+)"',
                r'blocked_by="([a-z_]+)"',
                r'"blocked_by":\s*"([a-z_]+)"',
                r'\breason\s*=\s*"([a-z_]+)"',
            ):
                found.update(re.findall(pat, src))
        # Sizing candidates in risk/math.py.
        math_src = (APP / "risk" / "math.py").read_text()
        found.update(re.findall(r'\("([a-z_]+)",\s*shares_for', math_src))
        # Literals that are not change reasons (other gates_result or
        # validation fields, outcome bookkeeping).
        not_change_reasons = {
            "gate",  # executor fallback — covered by CHANGE_REASONS["gate"]
            "checks",
            "no_call",
            "rules",
            "broker_close",
            "call_cap",
            "no_options",
            "tighten_noop",
        }
        # Outcome reason codes are not change reasons.
        not_change_reasons |= set(REASONS)
        for literal in sorted(found - not_change_reasons):
            assert change_reason_key(literal) is not None, (
                f"change reason {literal!r} ({path.name}) has no "
                "CHANGE_REASONS entry"
            )

    def test_kickoff_change_reasons_present(self) -> None:
        for key in (
            "rr_below_min",
            "stop_distance",
            "spread",
            "max_open_positions",
            "sector_cap",
            "total_open_risk",
            "open_risk",
            "edge_too_small",
            "flip_flop",
            "price_stale",
            "price_suspect",
            "not_selected",
            "kill_switch",
            "paused",
            "reconciliation",
            "already_held",
            "entry_pending",
            "daily_loss_limit",
            "shock_brake",
            "earnings_blackout",
            "fomc_window",
            "reentry",
            "drawdown_kill",
            "working_entry_unpriced",
            "risk_per_trade",
            "max_position",
            "cash",
            "order_sanity",
            "position_size",
            "trend_gate",
        ):
            assert key in CHANGE_REASONS, key

    def test_dynamic_check_names_resolve(self) -> None:
        for name in (
            "Reward / risk ≥ 1.50",
            "Stop 1.0–3.0 × ATR (1h)",
            "Confidence ≥ 0.40",
        ):
            assert change_reason_key(name) is not None


class TestAttention:
    """Step 3b: every rule on both sides of its threshold."""

    def test_success_is_never_flagged(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            call = _call(
                session,
                outcome="success",
                reasons=["slow", "timeout"],
                latency_ms=99_000,
            )
            session.flush()
            assert _attention(session, call) == "none"
            session.rollback()

    def test_api_error_auth_is_act(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            for status in (401, 403, 404):
                call = _call(
                    session,
                    outcome="error",
                    error_type="api_error",
                    http_status=status,
                    reasons=["api_error"],
                    sent_at=T0 + timedelta(minutes=status),
                )
                session.flush()
                assert _attention(session, call) == "act", status
            session.rollback()

    def test_api_error_429_needs_three_in_session(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            for i in range(2):
                _call(
                    session,
                    outcome="error",
                    error_type="api_error",
                    http_status=429,
                    reasons=["api_error"],
                    sent_at=T0 + timedelta(minutes=i),
                )
            third = _call(
                session,
                outcome="error",
                error_type="api_error",
                http_status=429,
                reasons=["api_error"],
                sent_at=T0 + timedelta(minutes=5),
            )
            session.flush()
            # The third 429 in one session is act; the first two watch
            # only via the provider-error rule — here they stay none
            # because no earlier provider error exists for #1 and #2
            # does see #1… assert the boundary on the third call.
            assert _attention(session, third) == "act"
            session.rollback()

    def test_429_two_is_not_act(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            second = None
            for i in range(2):
                second = _call(
                    session,
                    outcome="error",
                    error_type="api_error",
                    http_status=429,
                    reasons=["api_error"],
                    sent_at=T0 + timedelta(minutes=i),
                )
            session.flush()
            assert _attention(session, second) == "watch"  # 2nd provider error
            session.rollback()

    def test_timeout_three_cycles_is_act(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            calls = []
            for i in range(3):
                c = _call(
                    session,
                    outcome="error",
                    error_type="timeout",
                    reasons=["timeout"],
                    cycle_id=_cycle(session, T0 + timedelta(minutes=5 * i)),
                    sent_at=T0 + timedelta(minutes=5 * i),
                )
                calls.append(c)
            session.flush()
            assert _attention(session, calls[1]) != "act"  # 2 in a row
            assert _attention(session, calls[2]) == "act"  # 3 in a row
            session.rollback()

    def test_timeout_two_cycles_is_watch(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            first = _call(
                session,
                outcome="error",
                error_type="timeout",
                reasons=["timeout"],
                cycle_id=_cycle(session, T0),
                sent_at=T0,
            )
            second = _call(
                session,
                outcome="error",
                error_type="timeout",
                reasons=["timeout"],
                cycle_id=_cycle(session, T0 + timedelta(minutes=5)),
                sent_at=T0 + timedelta(minutes=5),
            )
            session.flush()
            assert _attention(session, first) == "none"
            assert _attention(session, second) == "watch"
            session.rollback()

    def test_error_streak_broken_by_a_success(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            for i in range(3):
                _call(
                    session,
                    outcome="error" if i < 2 else "success",
                    error_type="timeout" if i < 2 else None,
                    reasons=["timeout"] if i < 2 else [],
                    cycle_id=_cycle(session, T0 + timedelta(minutes=5 * i)),
                    sent_at=T0 + timedelta(minutes=5 * i),
                )
            fourth = _call(
                session,
                outcome="error",
                error_type="timeout",
                reasons=["timeout"],
                cycle_id=_cycle(session, T0 + timedelta(minutes=20)),
                sent_at=T0 + timedelta(minutes=20),
            )
            session.flush()
            # The success in the middle cycle breaks the run: this is
            # the first error in a row, so no act — but the earlier
            # timeout is within 2 cycles, so watch.
            assert _attention(session, fourth) == "watch"
            session.rollback()

    def test_input_incomplete_same_source_three_cycles(self) -> None:
        payload = {"news": {"status": "ok"}, "quote": "unavailable"}
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            rows = []
            for i in range(3):
                row = _call(
                    session,
                    outcome="warning",
                    reasons=["input_incomplete"],
                    cycle_id=_cycle(session, T0 + timedelta(minutes=5 * i)),
                    sent_at=T0 + timedelta(minutes=5 * i),
                    sent_input={"quote": "unavailable"},
                )
                rows.append(row)
            session.flush()
            assert _attention(session, rows[0], payload) == "none"
            assert _attention(session, rows[1], payload) == "none"
            assert _attention(session, rows[2], payload) == "act"
            session.rollback()

    def test_input_incomplete_different_source_not_act(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            rows = []
            for i, missing in enumerate(
                ("quote", "news_status", "fred")
            ):
                row = _call(
                    session,
                    outcome="warning",
                    reasons=["input_incomplete"],
                    cycle_id=_cycle(session, T0 + timedelta(minutes=5 * i)),
                    sent_at=T0 + timedelta(minutes=5 * i),
                    sent_input={missing: "unavailable"},
                )
                rows.append(row)
            session.flush()
            # Each cycle missed a different top-level source.
            assert _attention(session, rows[2], {"fred": "unavailable"}) != "act"
            session.rollback()

    def test_invalid_output_three_of_last_20_is_act(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            for i in range(19):
                _call(
                    session,
                    outcome="success" if i % 2 else "error",
                    reasons=["invalid_output"] if i % 2 == 0 else [],
                    error_type="invalid_output" if i % 2 == 0 else None,
                    sent_at=T0 - timedelta(minutes=30 - i),
                )
            # 10 bad of the previous 19 + this one = 11 of 20 → act.
            bad = _call(
                session,
                outcome="error",
                error_type="invalid_output",
                reasons=["invalid_output"],
                sent_at=T0,
            )
            session.flush()
            assert _attention(session, bad) == "act"
            session.rollback()

    def test_invalid_output_two_of_last_20_is_not_act(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            for i in range(19):
                _call(
                    session,
                    outcome="success",
                    sent_at=T0 - timedelta(minutes=30 - i),
                )
            # One earlier bad call among 19 + this one = 2 of 20.
            _call(
                session,
                outcome="error",
                error_type="invalid_output",
                reasons=["invalid_output"],
                sent_at=T0 - timedelta(minutes=25),
            )
            second = _call(
                session,
                outcome="error",
                error_type="invalid_output",
                reasons=["invalid_output"],
                sent_at=T0,
            )
            session.flush()
            assert _attention(session, second) != "act"
            session.rollback()

    def test_number_check_three_of_last_five_reviews(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            for i in range(4):
                _call(
                    session,
                    source="review",
                    kind="weekly",
                    outcome="warning",
                    reasons=["number_check_failed"],
                    sent_at=T0 - timedelta(days=4 - i),
                )
            fifth = _call(
                session,
                source="review",
                kind="weekly",
                outcome="warning",
                reasons=["number_check_failed"],
                sent_at=T0,
            )
            session.flush()
            # 3 of the last 5 reviews of this kind failed the check.
            assert _attention(session, fifth) == "act"
            session.rollback()

    def test_number_check_two_of_five_is_not_act(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            # Oldest → newest: ok, bad, ok, ok — with this call bad
            # the last five hold 2 failures.
            for i, bad in enumerate((False, True, False, False)):
                _call(
                    session,
                    source="review",
                    kind="briefing",
                    outcome="warning" if bad else "success",
                    reasons=["number_check_failed"] if bad else [],
                    sent_at=T0 - timedelta(days=4 - i),
                )
            fifth = _call(
                session,
                source="review",
                kind="briefing",
                outcome="warning",
                reasons=["number_check_failed"],
                sent_at=T0,
            )
            session.flush()
            assert _attention(session, fifth) == "none"
            session.rollback()

    def test_hold_below_breakeven_overnight_is_act(self) -> None:
        payload = {
            "position": {"opened_at": "2026-10-21T13:30:00+00:00"}
        }
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            call = _call(
                session,
                context="held",
                outcome="warning",
                reasons=["hold_below_breakeven"],
                sent_at=T0,
            )
            session.flush()
            assert _attention(session, call, payload) == "act"
            session.rollback()

    def test_hold_below_breakeven_same_day_is_not_act(self) -> None:
        payload = {
            "position": {"opened_at": "2026-10-22T13:30:00+00:00"}
        }
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            call = _call(
                session,
                context="held",
                outcome="warning",
                reasons=["hold_below_breakeven"],
                sent_at=T0,
            )
            session.flush()
            assert _attention(session, call, payload) == "none"
            session.rollback()

    def test_changed_by_code_is_never_flagged(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            call = _call(
                session,
                outcome="warning",
                reasons=["changed_by_code"],
                sent_at=T0,
            )
            session.flush()
            assert _attention(session, call) == "none"
            session.rollback()

    def test_slow_three_in_session_is_watch(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            rows = []
            for i in range(3):
                rows.append(
                    _call(
                        session,
                        outcome="warning",
                        reasons=["slow"],
                        latency_ms=20_000,
                        sent_at=T0 + timedelta(minutes=i),
                    )
                )
            session.flush()
            assert _attention(session, rows[0]) == "none"
            assert _attention(session, rows[1]) == "none"
            assert _attention(session, rows[2]) == "watch"
            session.rollback()

    def test_claim_warnings_20_percent_of_last_20(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            # 19 earlier decision calls, 4 with a claim warning (21%).
            for i in range(19):
                _call(
                    session,
                    outcome="warning" if i < 4 else "success",
                    reasons=["unsupported_claim"] if i < 4 else [],
                    sent_at=T0 - timedelta(minutes=40 - i),
                )
            flagged = _call(
                session,
                outcome="warning",
                reasons=["confidence_outlier"],
                sent_at=T0,
            )
            session.flush()
            # 5 of 20 = 25% ≥ 20% → watch.
            assert _attention(session, flagged) == "watch"
            session.rollback()

    def test_claim_warnings_below_share_is_none(self) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            for i in range(19):
                _call(
                    session,
                    outcome="warning" if i < 2 else "success",
                    reasons=["unsupported_claim"] if i < 2 else [],
                    sent_at=T0 - timedelta(minutes=40 - i),
                )
            flagged = _call(
                session,
                outcome="warning",
                reasons=["confidence_outlier"],
                sent_at=T0,
            )
            session.flush()
            # 3 of 20 = 15% < 20% → none.
            assert _attention(session, flagged) == "none"
            session.rollback()

    def test_stored_levels_never_rewrite(self) -> None:
        """A later write computes its own level; stored rows keep theirs."""
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            first = _call(
                session,
                outcome="error",
                error_type="timeout",
                reasons=["timeout"],
                cycle_id=_cycle(session, T0),
                sent_at=T0,
                attention="watch",
            )
            session.flush()
            stored = session.execute(
                select(LlmCall.attention).where(LlmCall.id == first.id)
            ).scalar_one()
            assert stored == "watch"
            # Computing attention for a later call does not touch it.
            later = _call(
                session,
                outcome="error",
                error_type="timeout",
                reasons=["timeout"],
                cycle_id=_cycle(session, T0 + timedelta(minutes=5)),
                sent_at=T0 + timedelta(minutes=5),
            )
            session.flush()
            _attention(session, later)
            session.flush()
            session.expire_all()
            assert session.get(LlmCall, first.id).attention == "watch"
            session.rollback()


class TestExplanations:
    """Step 2: one panel per reason, details from stored rows only."""

    def _items(self, call: LlmCall, **kw: Any) -> list[dict[str, Any]]:
        defaults: dict[str, Any] = {
            "stored": None,
            "change_reason": None,
            "change_detail": None,
            "touches_give_up_sessions": 2,
            "slow_call_seconds": 15.0,
            "resolve_link": lambda kind: f"/{kind}",
        }
        return explanation_items(call, **{**defaults, **kw})

    def test_changed_by_code_names_the_gate_and_numbers(self) -> None:
        call = LlmCall(
            sent_at=T0,
            session_date=DAY,
            source="live",
            kind="decision",
            outcome="warning",
            reasons=["changed_by_code"],
        )
        items = self._items(
            call,
            change_reason="rr_below_min",
            change_detail="reward to risk 1.3, minimum 1.5",
        )
        assert len(items) == 1
        item = items[0]
        assert item["code"] == "changed_by_code"
        assert item["means"] == REASONS["changed_by_code"].means
        assert "Reward to risk" in item["detail"]
        assert "1.3" in item["detail"]
        assert item["links"]

    def test_dynamic_change_reason_uses_stored_string(self) -> None:
        call = LlmCall(
            sent_at=T0,
            session_date=DAY,
            source="live",
            kind="decision",
            outcome="warning",
            reasons=["changed_by_code"],
        )
        item = self._items(
            call, change_reason="Reward / risk ≥ 1.50"
        )[0]
        assert "Reward to risk" in item["detail"]

    def test_input_incomplete_lists_unavailable_fields(self) -> None:
        call = LlmCall(
            sent_at=T0,
            session_date=DAY,
            source="live",
            kind="decision",
            outcome="warning",
            reasons=["input_incomplete"],
        )
        stored = {"quote": "unavailable", "news_status": "truncated"}
        item = self._items(call, stored=stored)[0]
        assert "quote" in item["detail"]
        assert "truncated" in item["detail"]

    def test_input_incomplete_verdict_touches_and_give_up(self) -> None:
        call = LlmCall(
            sent_at=T0,
            session_date=DAY,
            source="review",
            kind="verdict",
            outcome="warning",
            reasons=["input_incomplete"],
        )
        stored = {
            "kind": "verdict",
            "facts": {
                "price_5sessions_date": "2026-10-14",
                "options": [{"id": "O:1", "touch": "unavailable"}],
            },
        }
        item = self._items(call, stored=stored)[0]
        assert "option touches" in item["detail"]
        assert "unavailable by" in item["detail"]

    def test_timeout_detail_has_status_and_message(self) -> None:
        call = LlmCall(
            sent_at=T0,
            session_date=DAY,
            source="live",
            kind="decision",
            outcome="error",
            error_type="timeout",
            latency_ms=30_500,
            reasons=["timeout"],
        )
        item = self._items(call)[0]
        assert "timeout" in item["detail"]
        assert "30.5 s" in item["detail"]

    def test_api_error_picks_status_class(self) -> None:
        call = LlmCall(
            sent_at=T0,
            session_date=DAY,
            source="live",
            kind="decision",
            outcome="error",
            error_type="api_error",
            http_status=401,
            reasons=["api_error"],
        )
        item = self._items(call)[0]
        assert "key refused" in item["title"]
        assert "401 or 403" in item["cause"]

    def test_slow_detail_compares_to_threshold(self) -> None:
        call = LlmCall(
            sent_at=T0,
            session_date=DAY,
            source="live",
            kind="decision",
            outcome="warning",
            latency_ms=16_400,
            reasons=["slow"],
        )
        item = self._items(call)[0]
        assert "16.4 s" in item["detail"]
        assert "15" in item["detail"]

    def test_change_reason_and_detail_from_gates(self) -> None:
        gates = {
            "blocked_by": "max_open_positions",
            "checks": [
                {"name": "max_open_positions", "passed": False,
                 "detail": "3 open, max 3"},
            ],
        }
        reason, detail = change_reason_and_detail(gates, None)
        assert reason == "max_open_positions"
        assert detail == "3 open, max 3"

    def test_detail_for_reason_without_specifics_is_none(self) -> None:
        call = LlmCall(sent_at=T0, session_date=DAY, source="live",
                       kind="decision", outcome="warning", reasons=[])
        assert detail_for(
            "unsupported_claim", call=call, stored=None,
            change_reason=None, change_detail=None,
            touches_give_up_sessions=2, slow_call_seconds=15.0,
        ) is None


class TestApiSurface:
    """Summary top reasons, attention filter and per-cycle buckets."""

    @pytest.fixture()
    def seeded074(self, client: TestClient) -> None:
        with get_session_maker()() as session:
            session.execute(delete(LlmCall))
            cycle = Cycle(
                kind="cycle",
                scheduled_time=datetime(2026, 10, 22, 14, 0),
                started_at=datetime(2026, 10, 22, 14, 0),
                finished_at=datetime(2026, 10, 22, 14, 5),
                status="done",
            )
            session.add(cycle)
            session.flush()
            for i in range(3):
                _call(
                    session,
                    outcome="warning",
                    reasons=["changed_by_code"],
                    cycle_id=cycle.id,
                    sent_at=datetime(2026, 10, 22, 14, 0) + timedelta(minutes=i),
                )
            _call(
                session,
                outcome="error",
                error_type="timeout",
                reasons=["timeout"],
                attention="act",
                cycle_id=cycle.id,
                sent_at=datetime(2026, 10, 22, 14, 10),
            )
            _call(
                session,
                source="review",
                kind="weekly",
                outcome="warning",
                reasons=["number_check_failed"],
                attention="watch",
                sent_at=datetime(2026, 10, 22, 20, 0),
            )
            session.commit()

    def test_summary_attention_counts(
        self, client: TestClient, seeded074: None
    ) -> None:
        body = client.get("/api/llm-calls/summary").json()
        assert body["attention"] == {"none": 3, "watch": 1, "act": 1}

    def test_summary_top_reasons(
        self, client: TestClient, seeded074: None
    ) -> None:
        body = client.get("/api/llm-calls/summary").json()
        top = body["top_reasons"]
        assert top, "expected top reasons"
        assert top[0]["count"] >= top[-1]["count"]
        first = top[0]
        for key in ("reason", "count", "fix", "attention", "filter"):
            assert key in first
        assert first["filter"].startswith("reason=")

    def test_attention_filter(
        self, client: TestClient, seeded074: None
    ) -> None:
        body = client.get("/api/llm-calls?attention=act").json()
        assert body["total"] == 1
        assert body["calls"][0]["attention"] == "act"
        body = client.get("/api/llm-calls?attention=watch&kind=weekly").json()
        assert body["total"] == 1

    def test_attention_filter_in_summary(
        self, client: TestClient, seeded074: None
    ) -> None:
        body = client.get("/api/llm-calls/summary?attention=act").json()
        assert body["total"] == 1
        assert body["attention"]["act"] == 1

    def test_one_session_buckets_per_cycle(
        self, client: TestClient, seeded074: None
    ) -> None:
        body = client.get(
            "/api/llm-calls/summary?from=2026-10-22&to=2026-10-22"
        ).json()
        keys = [b["key"] for b in body["buckets"]]
        assert "after_close" in keys
        assert any(k.startswith("cycle:") for k in keys)
        cycle_bucket = next(b for b in body["buckets"] if b["key"].startswith("cycle:"))
        assert cycle_bucket["calls"] == 4
        after = next(b for b in body["buckets"] if b["key"] == "after_close")
        assert after["calls"] == 1

    def test_detail_has_explanations(
        self, client: TestClient, seeded074: None
    ) -> None:
        with get_session_maker()() as session:
            calls = session.execute(select(LlmCall)).scalars().all()
            call = next(c for c in calls if "timeout" in (c.reasons or []))
            call_id = call.id
        body = client.get(f"/api/llm-calls/{call_id}").json()
        assert "explanations" in body
        assert body["explanations"][0]["code"] == "timeout"
        assert body["explanations"][0]["means"]
