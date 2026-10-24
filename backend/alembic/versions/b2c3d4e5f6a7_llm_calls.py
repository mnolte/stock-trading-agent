"""llm_calls: one row per request actually sent to OpenAI (PR 18).

Live decisions, shadow decisions and reviews each write a row in the same
transaction as their linked row; this migration creates the table with the
spec's four indexes — (session_date, sent_at), (outcome, sent_at),
(source, sent_at), (symbol, sent_at) — and backfills it from existing
decisions, shadow_decisions and reviews with the same outcome rule the
writers use (docs/SPEC.md, LLM call history).

``verdicts.facts`` stores the facts a draft-verdict call was written from,
so the inspector can rebuild the user part of that prompt like it does
for reviews. ``verdicts.draft_reason`` records which fallback made a
template verdict (the reviews table already has ``reason``); the backfill
uses it plus ``review_fallback`` events to tell a call that was sent from
one that never happened.

Backfilled rows keep what older rows recorded: sent-at is the cycle's
scheduled time for live decisions and the row's created_at elsewhere;
latency lives on decisions and shadow_decisions; HTTP status, request id
and error type were never recorded, so a failed call maps to ``api_error``
and unknown fields stay null.

Revision ID: b2c3d4e5f6a7
Revises: b6c7d8e9f0a1
Create Date: 2026-10-24
"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op
from app.inspector.outcome import CallFacts, classify, input_incomplete

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "b6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NY = ZoneInfo("America/New_York")
# inspector.slow_call_seconds default (docs/SPEC.md, LLM call history).
_SLOW_MS = 15_000

# reviews.reason and review_fallback detail strings written before PR 18 —
# literal values, since old rows keep whatever was stored at the time.
_R_CALL_FAILED = "model call failed"
_R_INVALID = "invalid model output"
_R_NUMBER_CHECK = "number check failed"
_R_FACT_CHECK = "fact check failed"
# Fallbacks that prove a request was sent (a missing key or reached cap
# never sent one, so those rows stay out of llm_calls).
_CALL_OUTCOME: dict[str, tuple[str | None, bool, bool]] = {
    # reason -> (error_type, output_invalid, number_check_failed)
    _R_CALL_FAILED: ("api_error", False, False),
    _R_INVALID: (None, True, False),
    _R_NUMBER_CHECK: (None, False, True),
    _R_FACT_CHECK: (None, False, True),
}


def _dt(value: Any) -> datetime:
    """A stored timestamp — text() reads datetime columns back as str."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _session_date(sent_at: datetime) -> date:
    """The New York date a call belongs to (its sent instant)."""
    return sent_at.replace(tzinfo=UTC).astimezone(_NY).date()


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _facts(
    *,
    error_type: str | None = None,
    output_invalid: bool = False,
    model_action: str | None = None,
    final_action: str | None = None,
    snapshot: Any = None,
    number_check_failed: bool = False,
    latency_ms: int | None = None,
) -> CallFacts:
    return CallFacts(
        error_type=error_type,
        output_invalid=output_invalid,
        model_action=model_action,
        final_action=final_action,
        input_incomplete=input_incomplete(_json(snapshot)),
        number_check_failed=number_check_failed,
        latency_ms=latency_ms,
        slow_ms=_SLOW_MS,
    )


def _decision_call(row: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Outcome + reasons for a stored live or shadow decision."""
    validation = _json(row["validation_result"]) or {}
    failed = row["no_call_reason"] == "model_error"
    invalid = not failed and (
        row["model_output"] is None or validation.get("valid") is False
    )
    model_action = validation.get("model_action")
    facts = _facts(
        # Pre-PR18 calls never recorded timeout vs HTTP failure.
        error_type="api_error" if failed else None,
        output_invalid=invalid,
        model_action=model_action if isinstance(model_action, str) else None,
        final_action=row["final_action"],
        snapshot=row["input_snapshot"],
        latency_ms=row["latency_ms"],
    )
    outcome, reasons = classify(facts)
    return {
        "source": source,
        "kind": "decision",
        "final_action": row["final_action"],
        "outcome": outcome,
        "reasons": reasons,
        "error_type": reasons[0] if outcome == "error" else None,
    }


def _review_call(
    row: dict[str, Any], *, kind: str, reason: str | None
) -> dict[str, Any] | None:
    """Outcome + reasons for a review/verdict, or None when no call ran."""
    sent = _CALL_OUTCOME.get(reason or "")
    if row["source"] == "model":
        error_type, output_invalid, number_failed = None, False, False
    elif sent is not None:
        error_type, output_invalid, number_failed = sent
    else:
        return None
    outcome, reasons = classify(
        _facts(
            error_type=error_type,
            output_invalid=output_invalid,
            snapshot={"facts": _json(row.get("facts"))},
            number_check_failed=number_failed,
        )
    )
    return {
        "source": "review",
        "kind": kind,
        "final_action": kind,
        "outcome": outcome,
        "reasons": reasons,
        "error_type": reasons[0] if outcome == "error" else None,
    }


def _fallback_reason(
    conn: sa.engine.Connection, *, decision_id: int, symbol: str
) -> str | None:
    """Why a template verdict fell back, from its review_fallback event."""
    ref = f"decision {decision_id} ({symbol})"
    return conn.execute(
        sa.text(
            "SELECT json_extract(details, '$.reason') FROM events"
            " WHERE event_type = 'review_fallback'"
            "   AND json_extract(details, '$.kind') = 'verdict'"
            "   AND json_extract(details, '$.ref') = :ref"
            " ORDER BY time DESC LIMIT 1"
        ),
        {"ref": ref},
    ).scalar_one_or_none()


def _backfill(conn: sa.engine.Connection) -> None:
    llm_calls = sa.Table("llm_calls", sa.MetaData(), autoload_with=conn)
    rows: list[dict[str, Any]] = []

    def call_row(
        computed: dict[str, Any], sent_at: Any, **fields: Any
    ) -> None:
        sent = _dt(sent_at)
        cost = fields.get("cost")
        if cost is not None and not isinstance(cost, Decimal):
            # text() reads Numeric back as float; the column wants Decimal.
            fields["cost"] = Decimal(str(cost))
        rows.append(
            {
                # Every link/field key is always present so the bulk
                # insert binds cleanly; unknown stays null.
                "strategy_id": None,
                "decision_id": None,
                "shadow_decision_id": None,
                "review_id": None,
                "verdict_id": None,
                "cycle_id": None,
                "symbol": None,
                "context": None,
                "model": None,
                "temperature": None,
                "prompt_version": None,
                "prompt_hash": None,
                "tokens_in": None,
                "tokens_out": None,
                "cost": None,
                "cost_source": "priced",
                "latency_ms": None,
                "raw_response": None,
                "sent_at": sent,
                "session_date": _session_date(sent),
                "http_status": None,
                "error_code": None,
                "error_message": None,
                "request_id": None,
                "backfilled": True,
                **computed,
                **fields,
            }
        )

    # Live decisions: every row with a model output or a failed call. A
    # cached answer sent nothing; rules/cap/price no_calls sent nothing.
    for r in conn.execute(
        sa.text(
            "SELECT d.id, d.cycle_id, d.symbol, d.context, d.input_snapshot,"
            " d.validation_result, d.model_output, d.final_action,"
            " d.no_call_reason, d.prompt_version, d.prompt_hash, d.model,"
            " d.tokens_in, d.tokens_out, d.cost, d.cost_source,"
            " d.latency_ms, d.raw_response, c.scheduled_time"
            " FROM decisions d JOIN cycles c ON c.id = d.cycle_id"
            " WHERE d.cached = 0"
            "   AND (d.model_output IS NOT NULL"
            "        OR d.no_call_reason = 'model_error')"
        )
    ).mappings():
        call_row(
            _decision_call(dict(r), source="live"),
            r["scheduled_time"],
            decision_id=r["id"],
            cycle_id=r["cycle_id"],
            symbol=r["symbol"],
            context=r["context"],
            model=r["model"],
            prompt_version=r["prompt_version"],
            prompt_hash=r["prompt_hash"],
            tokens_in=r["tokens_in"],
            tokens_out=r["tokens_out"],
            cost=r["cost"],
            cost_source=r["cost_source"],
            latency_ms=r["latency_ms"],
            raw_response=r["raw_response"],
        )

    # Shadow decisions: model output or a failed call; missed work and
    # rules_only rows sent nothing. The input snapshot sits on the shadow
    # row only for synthetic quotes — else it is live's stored snapshot.
    for r in conn.execute(
        sa.text(
            "SELECT s.id, s.cycle_id, s.strategy_id, s.symbol, s.context,"
            " COALESCE(s.input_snapshot, d.input_snapshot) AS input_snapshot,"
            " s.validation_result, s.model_output, s.final_action,"
            " s.no_call_reason, s.prompt_version, s.prompt_hash, s.model,"
            " s.tokens_in, s.tokens_out, s.cost, s.cost_source,"
            " s.latency_ms, s.raw_response, s.created_at"
            " FROM shadow_decisions s"
            " LEFT JOIN decisions d ON d.id = s.live_decision_id"
            " WHERE s.missed = 0"
            "   AND (s.model_output IS NOT NULL"
            "        OR s.no_call_reason = 'model_error')"
        )
    ).mappings():
        call_row(
            _decision_call(dict(r), source="shadow"),
            r["created_at"],
            shadow_decision_id=r["id"],
            strategy_id=r["strategy_id"],
            cycle_id=r["cycle_id"],
            symbol=r["symbol"],
            context=r["context"],
            model=r["model"],
            prompt_version=r["prompt_version"],
            prompt_hash=r["prompt_hash"],
            tokens_in=r["tokens_in"],
            tokens_out=r["tokens_out"],
            cost=r["cost"],
            cost_source=r["cost_source"],
            latency_ms=r["latency_ms"],
            raw_response=r["raw_response"],
        )

    # Reviews: a model write always sent a call; a template write only
    # when its reason says a call ran and failed a check.
    for r in conn.execute(
        sa.text(
            "SELECT id, kind, facts, source, reason, model, prompt_version,"
            " cost, cost_source, created_at FROM reviews"
            " WHERE source = 'model'"
            "    OR (source = 'template' AND reason IN"
            "        ('model call failed', 'invalid model output',"
            "         'number check failed', 'fact check failed'))"
        )
    ).mappings():
        computed = _review_call(dict(r), kind=r["kind"], reason=r["reason"])
        if computed is not None:
            call_row(
                computed,
                r["created_at"],
                review_id=r["id"],
                model=r["model"],
                prompt_version=r["prompt_version"],
                cost=r["cost"],
                cost_source=r["cost_source"],
            )

    # Verdicts: draft_source names who wrote the draft; a template draft
    # only counts when its review_fallback event proves a call was sent.
    for r in conn.execute(
        sa.text(
            "SELECT v.id, v.decision_id, v.draft_source, v.draft_reason,"
            " v.cost, v.cost_source, v.facts, v.created_at, d.symbol"
            " FROM verdicts v JOIN decisions d ON d.id = v.decision_id"
        )
    ).mappings():
        reason = r["draft_reason"]
        if r["draft_source"] == "template" and reason is None:
            reason = _fallback_reason(
                conn, decision_id=r["decision_id"], symbol=r["symbol"]
            )
        computed = _review_call(
            {**r, "source": r["draft_source"]}, kind="verdict", reason=reason
        )
        if computed is not None:
            call_row(
                computed,
                r["created_at"],
                verdict_id=r["id"],
                decision_id=r["decision_id"],
                symbol=r["symbol"],
                cost=r["cost"],
                cost_source=r["cost_source"],
            )

    if rows:
        conn.execute(sa.insert(llm_calls), rows)


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("decision_id", sa.Integer(), sa.ForeignKey("decisions.id"), nullable=True),
        sa.Column(
            "shadow_decision_id",
            sa.Integer(),
            sa.ForeignKey("shadow_decisions.id"),
            nullable=True,
        ),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("reviews.id"), nullable=True),
        sa.Column("verdict_id", sa.Integer(), sa.ForeignKey("verdicts.id"), nullable=True),
        sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("cycles.id"), nullable=True),
        sa.Column("symbol", sa.String(length=16), nullable=True),
        sa.Column("context", sa.String(length=16), nullable=True),
        sa.Column("model", sa.String(length=32), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("prompt_version", sa.String(length=16), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(19, 8), nullable=True),
        sa.Column("cost_source", sa.String(length=16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("final_action", sa.String(length=32), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("error_type", sa.String(length=16), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("backfilled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # (sent_at, id) is the keyset order of the default list view.
    op.create_index("ix_llm_calls_sent_id", "llm_calls", ["sent_at", "id"])
    op.create_index("ix_llm_calls_session_sent", "llm_calls", ["session_date", "sent_at"])
    op.create_index("ix_llm_calls_outcome_sent", "llm_calls", ["outcome", "sent_at"])
    op.create_index("ix_llm_calls_source_sent", "llm_calls", ["source", "sent_at"])
    op.create_index("ix_llm_calls_symbol_sent", "llm_calls", ["symbol", "sent_at"])
    # The four spec indexes answer the list filters; these keep the
    # summary's aggregates, option counts and latency percentiles inside
    # the 300 ms budget on 100k calls (one covering scan, per-dimension
    # index scans, index-assisted offsets).
    op.create_index(
        "ix_llm_calls_aggregates",
        "llm_calls",
        ["session_date", "source", "outcome", "cost"],
    )
    op.create_index("ix_llm_calls_action", "llm_calls", ["final_action"])
    op.create_index("ix_llm_calls_model", "llm_calls", ["model"])
    op.create_index("ix_llm_calls_prompt", "llm_calls", ["prompt_version"])
    op.create_index("ix_llm_calls_strategy", "llm_calls", ["strategy_id"])
    op.create_index("ix_llm_calls_latency", "llm_calls", ["latency_ms"])
    op.create_index("ix_llm_calls_reasons", "llm_calls", ["reasons"])
    # The summary's option counts scan this single covering index.
    op.create_index(
        "ix_llm_calls_dims", "llm_calls",
        ["outcome", "source", "symbol", "final_action", "model",
         "prompt_version", "strategy_id"],
    )
    op.add_column("verdicts", sa.Column("facts", sa.JSON(), nullable=True))
    op.add_column(
        "verdicts", sa.Column("draft_reason", sa.String(length=64), nullable=True)
    )

    _backfill(op.get_bind())
    # Stats for the covering indexes — the inspector's summary picks
    # them when sqlite_stat1 exists (fresh databases get ANALYZE from
    # the engine's own upkeep).
    op.get_bind().execute(sa.text("ANALYZE llm_calls"))


def downgrade() -> None:
    op.drop_column("verdicts", "draft_reason")
    op.drop_column("verdicts", "facts")
    op.drop_index("ix_llm_calls_latency", table_name="llm_calls")
    op.drop_index("ix_llm_calls_strategy", table_name="llm_calls")
    op.drop_index("ix_llm_calls_prompt", table_name="llm_calls")
    op.drop_index("ix_llm_calls_model", table_name="llm_calls")
    op.drop_index("ix_llm_calls_action", table_name="llm_calls")
    op.drop_index("ix_llm_calls_aggregates", table_name="llm_calls")
    op.drop_index("ix_llm_calls_symbol_sent", table_name="llm_calls")
    op.drop_index("ix_llm_calls_source_sent", table_name="llm_calls")
    op.drop_index("ix_llm_calls_outcome_sent", table_name="llm_calls")
    op.drop_index("ix_llm_calls_session_sent", table_name="llm_calls")
    op.drop_index("ix_llm_calls_reasons", table_name="llm_calls")
    op.drop_index("ix_llm_calls_dims", table_name="llm_calls")
    op.drop_index("ix_llm_calls_sent_id", table_name="llm_calls")
    op.drop_table("llm_calls")
