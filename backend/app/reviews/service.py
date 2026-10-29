"""Review writing, scheduling and catch-up (docs/SPEC.md, Reviews).

The weekly review is due every Saturday at ``reviews.weekly_time`` in the
app timezone, covering Monday–Friday. The daily briefing is due at
``reviews.briefing_time_ny`` on trading days and can be turned off. A
review missed while the app was down is written at the next start with
its original date; a briefing older than its trading day is skipped.

Every write is a single transaction that inserts the review (or verdict)
row plus — when the model text could not be used — a ``review_fallback``
event naming the reason. The unique ``(kind, period)`` key keeps catch-up
idempotent. Reviews never change settings, prompts or trading.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import _naive_utc
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, Outcome, Review, Run, Verdict
from app.decide.client import ModelResponse, token_cost
from app.decide.prompt import Prompt, cached_prompt
from app.engine.state import ExecutionState, record_event
from app.inspector.record import build_llm_call
from app.llm.models import model_provider, provider_label
from app.market.calendar import NY_TZ, CalendarProvider
from app.market.clock import Clock
from app.notifications.service import Notifier, NullNotifier
from app.reviews.client import SCHEMAS, ReviewClient
from app.reviews.facts import (
    app_tz,
    briefing_facts,
    verdict_eligible,
    verdict_facts,
    weekly_facts,
    weekly_period,
)
from app.reviews.numbercheck import bad_numbers
from app.reviews.templates import briefing_status, plain_content
from app.signals.service import SignalService

logger = logging.getLogger(__name__)

REASON_NUMBER_CHECK = "number check failed"
REASON_FACT_CHECK = "fact check failed"
REASON_NO_KEY = "no OpenAI key"
REASON_CALL_FAILED = "model call failed"
REASON_INVALID = "invalid model output"
REASON_CAP = "cost cap reached"

# The latest prompt version per review kind; config can pin an older one.
_LATEST_PROMPT = {"weekly": "weekly.v2", "briefing": "briefing.v2", "verdict": "verdict.v2"}

# Briefing facts the model may never call "unavailable": they are always
# lists — empty means none, a null would mean unavailable and never occurs.
_STATUS_FACTS = (
    ("positions", "position"),
    ("earnings_today", "earnings today"),
    ("earnings_tomorrow", "earnings tomorrow"),
    ("banners", "banner"),
)

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Safety bound: never look back for missed reviews further than this.
_MAX_WEEKS_BACK = 12


@dataclass(frozen=True)
class Composed:
    """The text block for one review, and where it came from."""

    content: dict[str, Any]
    source: str  # model | template
    reason: str | None
    model: str | None
    prompt_version: str
    tokens: int | None
    cost: Decimal | None
    # The request that produced the text (or failed trying). None when no
    # call was sent — a reached cap or a missing key — so no llm_calls
    # row is written (docs/SPEC.md, LLM call history).
    response: ModelResponse | None = None
    sent_at: datetime | None = None
    prompt_hash: str | None = None
    detail: str | None = None


def _words(text: str) -> int:
    return len(text.split())


class ReviewService:
    """Writes reviews and draft verdicts; owns the review cost cap."""

    def __init__(
        self,
        *,
        client: ReviewClient | None,
        config: AppConfig,
        clock: Clock,
        calendar: CalendarProvider,
        market_data: MarketDataService | None = None,
        signals: SignalService | None = None,
        state: ExecutionState | None = None,
        session_factory: sessionmaker[Session] | None = None,
        notify: Callable[[set[str]], None] | None = None,
        notifier: Notifier | None = None,
        secrets: Callable[[], list[str]] | None = None,
        key_ready: Callable[[str], bool] | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.clock = clock
        self.calendar = calendar
        self.market_data = market_data
        self.signals = signals
        self.state = state
        self._session_factory = session_factory or get_session_maker()
        self._notify = notify or (lambda topics: None)
        self._notifier = notifier or NullNotifier()
        # Scrubbed stored keys for call-history error messages.
        self._secrets = secrets
        # provider -> stored key passes its test (simulated mode: True).
        self._key_ready = key_ready or (lambda provider: True)

    # ------------------------------------------------------------------
    # schedule helpers

    def _weekly_due_time(self) -> tuple[int, time]:
        weekday = _WEEKDAYS.get(
            str(self.config.get("reviews", "weekly_day", default="saturday"))
            .strip()
            .lower(),
            5,
        )
        raw = str(self.config.get("reviews", "weekly_time", default="10:00"))
        hour, minute = (int(p) for p in raw.split(":")[:2])
        return weekday, time(hour, minute)

    def weekly_due_instants(
        self, bound: datetime | None
    ) -> list[datetime]:
        """Weekly-review instants (aware UTC, newest first) at or before now,
        not older than ``bound``. Empty when the app never ran."""
        if bound is None:
            return []
        tz = app_tz(self.config)
        weekday, at = self._weekly_due_time()
        now = self.clock.now()
        local_today = now.astimezone(tz).date()
        latest = local_today - timedelta(
            days=(local_today.weekday() - weekday) % 7
        )
        bound = bound if bound.tzinfo is not None else bound.replace(tzinfo=ZoneInfo("UTC"))
        out: list[datetime] = []
        for _ in range(_MAX_WEEKS_BACK * 2):
            candidate = datetime.combine(latest, at, tzinfo=tz)
            if candidate < bound:
                break
            if candidate <= now:
                out.append(candidate.astimezone(ZoneInfo("UTC")))
            latest -= timedelta(days=7)
        return out

    def _briefing_slot(self, day: date) -> datetime | None:
        """The briefing's due instant (aware UTC) for trading day ``day``."""
        if self.calendar.session_for(day) is None:
            return None
        raw = str(
            self.config.get("reviews", "briefing_time_ny", default="09:00")
        )
        hour, minute = (int(p) for p in raw.split(":")[:2])
        return datetime.combine(day, time(hour, minute), tzinfo=NY_TZ).astimezone(
            ZoneInfo("UTC")
        )

    def _briefing_enabled(self) -> bool:
        return bool(
            self.config.get("reviews", "briefing_enabled", default=True)
        )

    def next_briefing_at(self) -> datetime | None:
        """The next briefing write instant (aware UTC), for the
        dashboard's "next briefing" line. ``None`` when briefings are off
        or no trading day is found within two weeks."""
        if not self._briefing_enabled():
            return None
        now = self.clock.now()
        day = now.astimezone(NY_TZ).date()
        for _ in range(15):
            slot = self._briefing_slot(day)
            if slot is not None and slot > now:
                return slot
            day += timedelta(days=1)
        return None

    # Registered review-prompt contracts (docs/SPEC.md, Decision layer —
    # Known prompt versions only). Adding a version requires code that
    # validates its output shape; unknown names raise.
    _REVIEW_CONTRACTS = {
        "weekly": {"weekly.v1", "weekly.v2"},
        "briefing": {"briefing.v1", "briefing.v2"},
        "verdict": {"verdict.v1", "verdict.v2"},
    }

    def _prompt(self, kind: str) -> Prompt:
        version = str(
            self.config.get(
                "reviews", "prompts", kind,
                default=_LATEST_PROMPT.get(kind, f"{kind}.v1"),
            )
        )
        if version not in self._REVIEW_CONTRACTS.get(kind, set()):
            from app.decide.prompt import UnknownPromptVersion

            raise UnknownPromptVersion(version)
        return cached_prompt(version)

    # ------------------------------------------------------------------
    # cost cap (independent of the decision budget)

    def monthly_spend(self, session: Session) -> Decimal:
        """Review-call spend in the current app-timezone calendar month.

        Verdict calls count too: the cap in the spec covers every review
        call, not only weekly reviews and briefings.
        """
        tz = app_tz(self.config)
        first = self.clock.now().astimezone(tz).date().replace(day=1)
        month_start = _naive_utc(datetime.combine(first, time.min, tzinfo=tz))
        reviews = session.execute(
            select(func.sum(Review.cost)).where(Review.created_at >= month_start)
        ).scalar_one()
        verdicts = session.execute(
            select(func.sum(Verdict.cost)).where(Verdict.created_at >= month_start)
        ).scalar_one()
        return Decimal(reviews or 0) + Decimal(verdicts or 0)

    def _cap_reached(self, session: Session) -> bool:
        cap = Decimal(
            str(self.config.get("reviews", "monthly_cost_cap_usd", default=2))
        )
        return self.monthly_spend(session) >= cap

    # ------------------------------------------------------------------
    # model or template

    def _fallback(
        self,
        session: Session,
        *,
        kind: str,
        ref: str,
        facts: dict[str, Any],
        reason: str,
        prompt: Prompt,
        detail: str | None = None,
        response: ModelResponse | None = None,
        sent_at: datetime | None = None,
    ) -> Composed:
        record_event(
            session,
            self.clock,
            level="warning",
            event_type="review_fallback",
            details={
                "kind": kind,
                "ref": ref,
                "reason": reason,
                **({"detail": detail} if detail else {}),
            },
        )
        logger.warning(
            "review fallback for %s %s: %s%s",
            kind,
            ref,
            reason,
            f" ({detail})" if detail else "",
        )
        return Composed(
            content=plain_content(kind, facts),
            source="template",
            reason=reason,
            model=None,
            prompt_version=prompt.version,
            tokens=None,
            cost=None,
            response=response,
            sent_at=sent_at,
            prompt_hash=prompt.sha256,
            detail=detail,
        )

    def _validate(self, kind: str, content: object) -> dict[str, Any] | None:
        """Shape- and limit-check the model's JSON; None when unusable."""
        if not isinstance(content, dict):
            return None
        if kind == "weekly":
            summary = content.get("summary")
            observations = content.get("observations")
            suggestion = content.get("suggestion")
            if not isinstance(summary, str) or not summary.strip():
                return None
            limit = int(
                self.config.get(
                    "reviews", "max_words", "weekly_summary", default=120
                )
            )
            if _words(summary) > limit:
                return None
            if not isinstance(observations, list) or len(observations) > 3:
                return None
            if not all(isinstance(o, str) for o in observations):
                return None
            # v1 suggestion is a string; v2 is {text, evidence} or null.
            if suggestion is not None and not (
                isinstance(suggestion, str)
                or (
                    isinstance(suggestion, dict)
                    and isinstance(suggestion.get("text"), str)
                    and isinstance(suggestion.get("evidence"), str)
                )
            ):
                return None
            return content
        if kind == "briefing":
            text = content.get("text")
            if not isinstance(text, str) or not text.strip():
                return None
            limit = int(
                self.config.get("reviews", "max_words", "briefing", default=80)
            )
            return content if _words(text) <= limit else None
        verdict = content.get("verdict")
        reason = content.get("reason")
        # verdict.v2: the label is code's draft_verdict; a model-sent
        # verdict field is ignored (schema rejects it outright).
        if verdict is not None and verdict not in ("good_call", "bad_call", "unclear"):
            return None
        if not isinstance(reason, str) or not reason.strip():
            return None
        thesis_check = content.get("thesis_check")
        if thesis_check is not None and thesis_check not in (
            "consistent", "contradicts", "unclear"
        ):
            return None
        contradiction = content.get("contradiction")
        if contradiction is not None and not isinstance(contradiction, str):
            return None
        return content

    @staticmethod
    def _content_texts(content: dict[str, Any]) -> list[str]:
        """Every text field the number check scans."""
        texts: list[str] = []
        for value in content.values():
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, list):
                texts.extend(v for v in value if isinstance(v, str))
            elif isinstance(value, dict):
                texts.extend(v for v in value.values() if isinstance(v, str))
        return texts

    async def _compose(
        self,
        session: Session,
        *,
        kind: str,
        ref: str,
        facts: dict[str, Any],
    ) -> Composed:
        """Model text when possible, else the plain template with a reason."""
        prompt = self._prompt(kind)
        if self._cap_reached(session):
            return self._fallback(
                session, kind=kind, ref=ref, facts=facts,
                reason=REASON_CAP, prompt=prompt
            )
        if self.client is None:
            return self._fallback(
                session, kind=kind, ref=ref, facts=facts,
                reason=REASON_NO_KEY, prompt=prompt
            )
        model = self._model_for(kind)
        if not self._key_ready(model_provider(self.config, model)):
            return self._fallback(
                session,
                kind=kind,
                ref=ref,
                facts=facts,
                reason=f"no {provider_label(model_provider(self.config, model))} key",
                prompt=prompt,
            )
        sent_at = _naive_utc(self.clock.now())
        response = await self.client.complete(
            system=prompt.text,
            user=json.dumps({"kind": kind, "facts": facts}, indent=2),
            schema=SCHEMAS.get(prompt.version, SCHEMAS[kind]),
            model=model,
            temperature=float(
                self.config.get("reviews", "temperature", default=0.2)
            ),
        )
        if (
            response.error is not None
            or response.refused
            or response.raw is None
        ):
            return self._fallback(
                session,
                kind=kind,
                ref=ref,
                facts=facts,
                reason=REASON_CALL_FAILED,
                prompt=prompt,
                detail=response.error or "refusal",
                response=response,
                sent_at=sent_at,
            )
        try:
            parsed: object = json.loads(response.raw)
        except json.JSONDecodeError:
            parsed = None
        content = self._validate(kind, parsed)
        if content is None:
            return self._fallback(
                session,
                kind=kind,
                ref=ref,
                facts=facts,
                reason=REASON_INVALID,
                prompt=prompt,
                detail=(response.raw or "")[:200],
                response=response,
                sent_at=sent_at,
            )
        text = "\n".join(self._content_texts(content)).lower()
        # A fact the code produced — an empty list included — is known,
        # never "unavailable"; the model may not call it that. Clause
        # boundaries include commas and dashes so a legitimate "unavailable"
        # for a null fact (regime, yesterday's result) in the same sentence
        # does not fail the check.
        if kind == "briefing":
            clauses = (
                text.replace(";", ".")
                .replace("\n", ".")
                .replace(",", ".")
                .replace("—", ".")
                .replace("–", ".")
                .split(".")
            )
            false_claims = [
                clause.strip()
                for clause in clauses
                if "unavailable" in clause
                and any(
                    isinstance(facts.get(key), list) and label in clause
                    for key, label in _STATUS_FACTS
                )
            ]
            if false_claims:
                return self._fallback(
                    session,
                    kind=kind,
                    ref=ref,
                    facts=facts,
                    reason=REASON_FACT_CHECK,
                    prompt=prompt,
                    detail="; ".join(false_claims)[:200],
                    response=response,
                    sent_at=sent_at,
                )
        bad = bad_numbers("\n".join(self._content_texts(content)), facts)
        if bad:
            return self._fallback(
                session,
                kind=kind,
                ref=ref,
                facts=facts,
                reason=REASON_NUMBER_CHECK,
                prompt=prompt,
                detail=", ".join(bad),
                response=response,
                sent_at=sent_at,
            )
        if (
            kind == "weekly"
            and content.get("suggestion") is not None
            and facts.get("suggestion_allowed") is False
        ):
            # The sample is too small for a suggestion; drop it (INFO).
            logger.info(
                "weekly suggestion dropped: fewer than %d closed trades in the run",
                int(
                    self.config.get(
                        "reviews", "min_closed_trades_for_suggestion", default=10
                    )
                ),
            )
            content = {**content, "suggestion": None}
        return Composed(
            content=content,
            source="model",
            reason=None,
            model=response.model,
            prompt_version=prompt.version,
            tokens=response.tokens,
            cost=token_cost(
                response,
                self.config,
                model=self._model_for(kind),
            ),
            response=response,
            sent_at=sent_at,
            prompt_hash=prompt.sha256,
        )

    def _model_for(self, kind: str) -> str:
        """The model configured for this review task."""
        from app.llm.models import review_model

        return review_model(self.config, kind)

    def _record_call(
        self,
        session: Session,
        composed: Composed,
        *,
        kind: str,
        facts: dict[str, Any],
        review_id: int | None = None,
        verdict_id: int | None = None,
    ) -> None:
        """Write the llm_calls row when a request was actually sent.

        A cap reached or a missing key means ``_compose`` never sent one —
        no row (docs/SPEC.md, LLM call history — What counts as a call).
        The fact check maps to ``number_check_failed``: a code check
        rejected the text and the plain template was used.
        """
        response = composed.response
        if response is None or composed.sent_at is None:
            return
        session.add(
            build_llm_call(
                source="review",
                kind=kind,
                sent_at=composed.sent_at,
                response=response,
                input_snapshot={"kind": kind, "facts": facts},
                # A review's "action" is its kind; a code check that fell
                # back to the template is a warning reason, not a rename.
                model_action=kind,
                final_action=kind,
                output_invalid=composed.reason == REASON_INVALID,
                error_detail=composed.detail,
                number_check_failed=composed.reason
                in (REASON_NUMBER_CHECK, REASON_FACT_CHECK),
                review_id=review_id,
                verdict_id=verdict_id,
                model=response.model,
                temperature=float(
                    self.config.get("reviews", "temperature", default=0.2)
                ),
                prompt_version=composed.prompt_version,
                prompt_hash=composed.prompt_hash,
                cost=composed.cost,
                slow_ms=int(
                    float(
                        self.config.get(
                            "inspector", "slow_call_seconds", default=15
                        )
                    )
                    * 1000
                ),
                scrub_values=self._secrets,
            )
        )

    # ------------------------------------------------------------------
    # weekly review

    async def write_weekly_due(self) -> Review | None:
        """Cron entry: write the most recent due weekly review, if missing."""
        bound = self._run_bound()
        instants = self.weekly_due_instants(bound)
        if not instants:
            return None
        return await self.write_weekly_for(instants[0])

    async def write_weekly_for(self, due: datetime) -> Review | None:
        """Write the weekly review whose due instant is ``due`` (aware UTC)."""
        period, monday, _friday = weekly_period(due)
        with self._session_factory() as session:
            existing = session.execute(
                select(Review).where(
                    Review.kind == "weekly", Review.period == period
                )
            ).scalar_one_or_none()
            if existing is not None:
                return None
            facts = weekly_facts(
                session,
                config=self.config,
                clock=self.clock,
                calendar=self.calendar,
                monday=monday,
            )
            composed = await self._compose(
                session, kind="weekly", ref=period, facts=facts
            )
            review = Review(
                kind="weekly",
                period=period,
                facts=facts,
                text=str(composed.content.get("summary", "")),
                content=composed.content,
                source=composed.source,
                reason=composed.reason,
                model=composed.model,
                prompt_version=composed.prompt_version,
                tokens=composed.tokens,
                cost=composed.cost,
                due_at=_naive_utc(due),
                created_at=_naive_utc(self.clock.now()),
            )
            session.add(review)
            session.flush()
            self._record_call(
                session, composed, kind="weekly", facts=facts,
                review_id=review.id,
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return None
        self._notifier.notify(
            "weekly_review",
            f"week {period} ready",
            body=str(composed.content.get("summary", "")),
            link="/reviews",
        )
        self._notify({"reviews"})
        return review

    # ------------------------------------------------------------------
    # daily briefing

    async def write_briefing_due(self) -> Review | None:
        """Cron entry: write today's briefing when its slot has passed."""
        if not self._briefing_enabled():
            return None
        day = self.clock.now().astimezone(NY_TZ).date()
        slot = self._briefing_slot(day)
        if slot is None or self.clock.now() < slot:
            return None
        return await self.write_briefing_for(day)

    async def write_briefing_for(self, day: date) -> Review | None:
        """Write the briefing for trading day ``day`` (New York date)."""
        period = day.isoformat()
        due = self._briefing_slot(day)
        if due is None:
            return None
        with self._session_factory() as session:
            existing = session.execute(
                select(Review).where(
                    Review.kind == "briefing", Review.period == period
                )
            ).scalar_one_or_none()
            if existing is not None:
                return None
            facts = briefing_facts(
                session,
                config=self.config,
                clock=self.clock,
                calendar=self.calendar,
                market_data=self.market_data,
                signals=self.signals,
                state=self.state,
                day=day,
            )
            composed = await self._compose(
                session, kind="briefing", ref=period, facts=facts
            )
            composed.content["status_line"] = briefing_status(facts)
            if composed.source == "model":
                composed.content["text"] = (
                    briefing_status(facts) + " " + str(composed.content.get("text", ""))
                )
            review = Review(
                kind="briefing",
                period=period,
                facts=facts,
                text=str(composed.content.get("text", "")),
                content=composed.content,
                source=composed.source,
                reason=composed.reason,
                model=composed.model,
                prompt_version=composed.prompt_version,
                tokens=composed.tokens,
                cost=composed.cost,
                due_at=_naive_utc(due),
                created_at=_naive_utc(self.clock.now()),
            )
            session.add(review)
            session.flush()
            self._record_call(
                session, composed, kind="briefing", facts=facts,
                review_id=review.id,
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return None
            waiting = session.execute(
                select(func.count(Verdict.id)).where(
                    Verdict.source == "draft"
                )
            ).scalar_one()
        self._notifier.notify(
            "daily_briefing",
            f"{period} ready",
            body=str(composed.content.get("text", "")),
            link="/reviews",
        )
        if waiting:
            # Spec: once a day, with the briefing.
            self._notifier.notify(
                "verdicts_waiting",
                f"{waiting} draft verdict(s) waiting to confirm",
                link="/reviews",
            )
        self._notify({"reviews"})
        return review

    # ------------------------------------------------------------------
    # draft verdicts

    async def write_due_verdicts(self) -> int:
        """A draft verdict for every eligible decision whose 5-session
        outcome is in and that has no verdict row yet. Returns the count."""
        with self._session_factory() as session:
            rows = session.execute(
                select(Decision, Cycle.scheduled_time, Outcome)
                .join(Cycle, Decision.cycle_id == Cycle.id)
                .join(Outcome, Outcome.decision_id == Decision.id)
                .outerjoin(Verdict, Verdict.decision_id == Decision.id)
                .where(
                    Outcome.price_5sessions.is_not(None),
                    Verdict.id.is_(None),
                    Decision.final_action.in_(("enter", "skip")),
                    Decision.no_call_reason.is_(None),
                )
                .order_by(Cycle.scheduled_time)
            ).all()
            written = 0
            now = _naive_utc(self.clock.now())
            for decision, scheduled, outcome in rows:
                if not verdict_eligible(decision):
                    continue
                facts = verdict_facts(
                    session,
                    config=self.config,
                    clock=self.clock,
                    decision=decision,
                    scheduled=scheduled,
                    outcome=outcome,
                )
                ref = f"decision {decision.id} ({decision.symbol})"
                composed = await self._compose(
                    session, kind="verdict", ref=ref, facts=facts
                )
                # The label always comes from code's draft_verdict; the
                # model writes the reason and the thesis check only.
                verdict_label = str(
                    facts.get("draft_verdict") or composed.content["verdict"]
                )
                thesis_check = composed.content.get("thesis_check")
                verdict = Verdict(
                    decision_id=decision.id,
                    verdict=verdict_label,
                    reason=str(composed.content["reason"]),
                    thesis_check=(
                        str(thesis_check)
                        if isinstance(thesis_check, str)
                        else None
                    ),
                    contradiction=(
                        str(composed.content["contradiction"])
                        if isinstance(composed.content.get("contradiction"), str)
                        else None
                    ),
                    source="draft",
                    draft_source=composed.source,
                    draft_reason=composed.reason,
                    cost=composed.cost,
                    facts=facts,
                    created_at=now,
                    updated_at=now,
                )
                session.add(verdict)
                session.flush()
                self._record_call(
                    session, composed, kind="verdict", facts=facts,
                    verdict_id=verdict.id,
                )
                # Commit per verdict: the cap counts this call's spend
                # before the next compose, and no write transaction stays
                # open across a model call (AGENTS.md, hard rule 13).
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    continue
                written += 1
        if written:
            self._notify({"reviews", "decisions"})
        return written

    # ------------------------------------------------------------------
    # missed runs

    def _run_bound(self) -> datetime | None:
        """Earliest run start: reviews are only due once the app has run."""
        with self._session_factory() as session:
            bound = session.execute(
                select(func.min(Run.started_at))
            ).scalar()
        if bound is None:
            return None
        return bound if bound.tzinfo is not None else bound.replace(
            tzinfo=ZoneInfo("UTC")
        )

    async def catch_up(self) -> dict[str, int]:
        """Startup pass: missed weekly reviews (original dates), today's
        briefing if its slot passed, and verdicts due while the app was
        down. A briefing older than its trading day is never written."""
        written = {"weekly": 0, "briefing": 0, "verdicts": 0}
        for due in reversed(self.weekly_due_instants(self._run_bound())):
            if await self.write_weekly_for(due) is not None:
                written["weekly"] += 1
        if await self.write_briefing_due() is not None:
            written["briefing"] += 1
        written["verdicts"] = await self.write_due_verdicts()
        return written
