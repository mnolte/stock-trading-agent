"""Insights → Model costs (item 109, docs/SPEC.md → Model costs).

Computed on request — never in the nightly snapshot — from ``llm_calls``
(one grouped query over the (source, sent_at) index) and ``backtests``.
Costs are the stored values; a row without the item-109 cache columns
counts as uncached and is never recomputed.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, case, func, select

from app.db.models import Backtest, Bar1d, LlmCall, Strategy
from app.llm.models import model_provider, models_table, provider_label
from app.market.clock import NY_TZ

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.config import AppConfig

PERIODS = ("this_month", "last_month", "3_months", "all")

# The spec's purpose order; ``total`` is appended by the caller.
_PURPOSE_ORDER = (
    "live",
    "shadow",
    "weekly",
    "briefing",
    "verdict",
    "ask",
    "backtest",
)

_PURPOSE_LABELS = {
    "live": "Live decisions",
    "shadow": "Shadows",
    "weekly": "Weekly review",
    "briefing": "Daily briefing",
    "verdict": "Verdicts",
    "ask": "Ask",
    "backtest": "Backtests",
}

# Backtest statuses whose spend is final (docs/SPEC.md, Backtests).
_TERMINAL_STATUSES = (
    "done",
    "cancelled",
    "stopped_at_cost_cap",
    "interrupted",
    "refused",
)

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _months_back(day: date, count: int) -> date:
    """``count`` calendar months before ``day`` (day clamped to month end)."""
    index = day.year * 12 + day.month - 1 - count
    year, month0 = divmod(index, 12)
    month = month0 + 1
    last = [
        31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31, 30, 31, 30, 31, 31, 30, 31, 30, 31,
    ][month - 1]
    return date(year, month, min(day.day, last))


def period_range(period: str, today: date) -> tuple[date | None, date]:
    """(first day, last day) of the New York-calendar period, inclusive."""
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if period == "3_months":
        return _months_back(today, 3), today
    return None, today  # "all"


def _utc_bounds(
    day_from: date | None, day_to: date
) -> tuple[datetime | None, datetime]:
    """NY-midnight bounds as naive UTC for the ``sent_at`` comparisons."""
    end = datetime.combine(day_to + timedelta(days=1), time.min, tzinfo=NY_TZ)
    start = (
        datetime.combine(day_from, time.min, tzinfo=NY_TZ)
        if day_from is not None
        else None
    )
    return (
        start.astimezone(UTC).replace(tzinfo=None) if start else None,
        end.astimezone(UTC).replace(tzinfo=None),
    )


def _money(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _num(value: Any) -> int:
    return int(value or 0)


def _purpose_case() -> Any:
    """SQL CASE mapping an ``llm_calls`` row to its spec purpose."""
    return case(
        (LlmCall.source == "live", "live"),
        (LlmCall.source == "shadow", "shadow"),
        (LlmCall.source == "ask", "ask"),
        (and_(LlmCall.source == "review", LlmCall.kind == "weekly"), "weekly"),
        (and_(LlmCall.source == "review", LlmCall.kind == "briefing"), "briefing"),
        (and_(LlmCall.source == "review", LlmCall.kind == "verdict"), "verdict"),
        else_="other",
    )


def _session_case() -> Any:
    """The four cost-per-session buckets (reviews = all three kinds)."""
    return case(
        (LlmCall.source == "live", "live"),
        (LlmCall.source == "shadow", "shadows"),
        (LlmCall.source == "review", "reviews"),
        (LlmCall.source == "ask", "ask"),
        else_="other",
    )


def _empty_metrics() -> dict[str, Any]:
    return {
        "calls": 0,
        "tokens_in": 0,
        "tokens_cached_in": 0,
        "tokens_cache_write_in": 0,
        "tokens_out": 0,
        "cost": Decimal(0),
        "cache_saved": Decimal(0),
        "cost_unknown": False,
    }


def _add_metrics(bucket: dict[str, Any], metrics: dict[str, Any]) -> None:
    for key in (
        "calls",
        "tokens_in",
        "tokens_cached_in",
        "tokens_cache_write_in",
        "tokens_out",
        "cost",
        "cache_saved",
    ):
        bucket[key] += metrics[key]
    bucket["cost_unknown"] = bucket["cost_unknown"] or metrics["cost_unknown"]


def _metrics_kwargs(
    calls: Any,
    tokens_in: Any,
    tokens_cached: Any,
    tokens_write: Any,
    tokens_out: Any,
    cost: Any,
    cache_saved: Any,
    null_costs: Any = 0,
) -> dict[str, Any]:
    return {
        "calls": _num(calls),
        "tokens_in": _num(tokens_in),
        "tokens_cached_in": _num(tokens_cached),
        "tokens_cache_write_in": _num(tokens_write),
        "tokens_out": _num(tokens_out),
        "cost": Decimal(str(cost or 0)),
        "cache_saved": Decimal(str(cache_saved or 0)),
        "cost_unknown": bool(null_costs),
    }


def _fmt_money(value: Decimal) -> str:
    """``$18.15`` / ``$5`` — whole dollars drop the cents (answer text)."""
    if value == value.to_integral_value():
        return f"${int(value):,}"
    return f"${value:,.2f}"


def _money2(value: Decimal) -> str:
    return f"${value:,.2f}"


# The 31-bar cap of the Cost per session chart (item 112): one bar per
# session up to 31 sessions, per ISO week up to 31 weeks, per month up
# to 31 months, per quarter beyond.
MAX_SESSION_BUCKETS = 31

_MONTHS_SHORT = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _month_end(day: date) -> date:
    """The last day of ``day``'s calendar month."""
    nxt = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def bucket_unit(sessions: Sequence[date]) -> str:
    """``session`` | ``week`` | ``month`` | ``quarter`` for a period's
    New York session days (item 112). ``sessions`` need not be sorted."""
    days = sorted(set(sessions))
    if len(days) <= MAX_SESSION_BUCKETS:
        return "session"
    first, last = days[0], days[-1]
    first_monday = first - timedelta(days=first.weekday())
    last_monday = last - timedelta(days=last.weekday())
    weeks = (last_monday - first_monday).days // 7 + 1
    if weeks <= MAX_SESSION_BUCKETS:
        return "week"
    months = (last.year - first.year) * 12 + last.month - first.month + 1
    if months <= MAX_SESSION_BUCKETS:
        return "month"
    return "quarter"


def _bucket_span(day: date, unit: str) -> tuple[str, str, date, date]:
    """(key, label, first, last) of the ``unit`` bucket holding ``day`` —
    calendar bounds; the row's own dates come from its sessions."""
    if unit == "session":
        return day.isoformat(), str(day.day), day, day
    if unit == "week":
        monday = day - timedelta(days=day.weekday())
        iso = day.isocalendar()
        return (
            f"{iso.year}-W{iso.week:02d}",
            f"W{iso.week:02d}",
            monday,
            monday + timedelta(days=6),
        )
    if unit == "month":
        return (
            f"{day.year}-{day.month:02d}",
            _MONTHS_SHORT[day.month - 1],
            day.replace(day=1),
            _month_end(day),
        )
    quarter = (day.month - 1) // 3 + 1
    return (
        f"{day.year}-Q{quarter}",
        f"Q{quarter}",
        date(day.year, (quarter - 1) * 3 + 1, 1),
        _month_end(date(day.year, quarter * 3, 1)),
    )


def _iter_bucket_days(first: date, last: date, unit: str) -> Iterator[date]:
    """One representative day per bucket from ``first``'s to ``last``'s."""
    if unit == "session":
        day = first
        while day <= last:
            yield day
            day += timedelta(days=1)
    elif unit == "week":
        day = first - timedelta(days=first.weekday())
        end = last - timedelta(days=last.weekday())
        while day <= end:
            yield day
            day += timedelta(days=7)
    elif unit == "month":
        year, month = first.year, first.month
        while (year, month) <= (last.year, last.month):
            yield date(year, month, 1)
            month += 1
            if month == 13:
                year, month = year + 1, 1
    else:  # quarter
        year, quarter = first.year, (first.month - 1) // 3
        last_key = (last.year, (last.month - 1) // 3)
        while (year, quarter) <= last_key:
            yield date(year, quarter * 3 + 1, 1)
            quarter += 1
            if quarter == 4:
                year, quarter = year + 1, 0


def _cap_monthly(used: Decimal, limit: Any) -> dict[str, Any] | None:
    if limit is None:
        return None
    limit_d = Decimal(str(limit))
    pct = (
        float((used / limit_d * 100).quantize(Decimal("0.1")))
        if limit_d > 0
        else None
    )
    return {
        "kind": "monthly",
        "used": _money(used),
        "limit": str(limit_d),
        "pct": pct,
    }


def model_costs(
    config: AppConfig, session: Session, period: str, *, today: date
) -> dict[str, Any]:
    """The whole Model costs payload for one period."""
    day_from, day_to = period_range(period, today)
    start_utc, end_utc = _utc_bounds(day_from, day_to)
    where = and_(LlmCall.sent_at < end_utc, LlmCall.sent_at >= start_utc) if (
        start_utc is not None
    ) else LlmCall.sent_at < end_utc

    # --- one grouped pass over llm_calls: purpose × model -------------
    purpose_col = _purpose_case()
    grouped = session.execute(
        select(
            purpose_col,
            LlmCall.model,
            func.count(LlmCall.id),
            func.coalesce(func.sum(LlmCall.tokens_in), 0),
            func.coalesce(func.sum(LlmCall.tokens_cached_in), 0),
            func.coalesce(func.sum(LlmCall.tokens_cache_write_in), 0),
            func.coalesce(func.sum(LlmCall.tokens_out), 0),
            func.coalesce(func.sum(LlmCall.cost), Decimal(0)),
            func.coalesce(func.sum(LlmCall.cache_saved), Decimal(0)),
            func.sum(case((LlmCall.cost.is_(None), 1), else_=0)),
        )
        .where(where)
        .group_by(purpose_col, LlmCall.model)
    ).all()

    purposes: dict[str, dict[str, Any]] = {
        key: _empty_metrics() for key in _PURPOSE_ORDER
    }
    other = _empty_metrics()
    # model -> aggregate for the By-model table.
    model_rows: dict[str, dict[str, Any]] = {}
    for (
        purpose,
        model,
        calls,
        t_in,
        t_cached,
        t_write,
        t_out,
        cost,
        saved,
        null_costs,
    ) in grouped:
        metrics = _metrics_kwargs(
            calls, t_in, t_cached, t_write, t_out, cost, saved, null_costs
        )
        bucket = purposes.get(str(purpose), other)
        _add_metrics(bucket, metrics)
        mrow = model_rows.setdefault(
            str(model or "unknown"), _empty_metrics() | {"purposes": set()}
        )
        _add_metrics(mrow, metrics)
        if str(purpose) != "other":
            mrow["purposes"].add(str(purpose))

    # --- shadow strategy names for the note ---------------------------
    shadow_ids = [
        int(r[0] or 0)
        for r in session.execute(
            select(LlmCall.strategy_id)
            .where(LlmCall.source == "shadow")
            .where(where)
            .where(LlmCall.strategy_id.is_not(None))
            .distinct()
        )
    ]
    shadow_strategies: list[dict[str, Any]] = []
    if shadow_ids:
        for strat in session.execute(
            select(Strategy).where(Strategy.id.in_(shadow_ids)).order_by(Strategy.id)
        ).scalars():
            shadow_strategies.append(
                {
                    "name": strat.name,
                    "model": strat.model,
                    "prompt_version": strat.prompt_version,
                }
            )

    # --- Ask questions -------------------------------------------------
    ask_questions = _num(
        session.execute(
            select(func.count(func.distinct(LlmCall.ask_message_id))).where(
                LlmCall.source == "ask",
                where,
                LlmCall.ask_message_id.is_not(None),
            )
        ).scalar_one()
    )

    # --- backtests -----------------------------------------------------
    bt_stamp = func.coalesce(Backtest.finished_at, Backtest.created_at)
    bt_conds = [bt_stamp < end_utc, Backtest.status.in_(_TERMINAL_STATUSES)]
    if start_utc is not None:
        bt_conds.append(bt_stamp >= start_utc)
    backtests = session.execute(
        select(
            Backtest.id,
            Backtest.cost_usd,
            Backtest.summary,
        ).where(and_(*bt_conds))
    ).all()

    bt = purposes["backtest"]
    bt_runs: list[int] = []
    bt_answers_reused = 0
    bt_without_usage = 0
    for run_id, cost_usd, summary in backtests:
        bt_runs.append(int(run_id))
        bt["cost"] += Decimal(str(cost_usd or 0))
        usage = (summary or {}).get("model_usage")
        if not usage:
            bt_without_usage += 1
            continue
        for entry in usage:
            model = str(entry.get("model") or "unknown")
            mrow = model_rows.setdefault(
                model, _empty_metrics() | {"purposes": set()}
            )
            mrow["purposes"].add("backtest")
            cost_val = entry.get("cost")
            _add_metrics(
                mrow,
                _metrics_kwargs(
                    entry.get("calls"),
                    entry.get("tokens_in"),
                    entry.get("tokens_cached_in"),
                    entry.get("tokens_cache_write_in"),
                    entry.get("tokens_out"),
                    cost_val if cost_val is not None else Decimal(0),
                    entry.get("cache_saved"),
                    1 if cost_val is None else 0,
                ),
            )
            bt["calls"] += _num(entry.get("calls"))
            bt["tokens_in"] += _num(entry.get("tokens_in"))
            bt["tokens_cached_in"] += _num(entry.get("tokens_cached_in"))
            bt["tokens_cache_write_in"] += _num(
                entry.get("tokens_cache_write_in")
            )
            bt["tokens_out"] += _num(entry.get("tokens_out"))
            bt["cache_saved"] += Decimal(str(entry.get("cache_saved") or 0))
            bt["cost_unknown"] = bt["cost_unknown"] or cost_val is None
            bt_answers_reused += _num(entry.get("answers_reused"))

    # --- New York sessions in the period -------------------------------
    day_conds = []
    if day_from is not None:
        day_conds.append(Bar1d.date >= day_from)
    day_conds.append(Bar1d.date <= day_to)
    session_days = [
        d
        for (d,) in session.execute(
            select(Bar1d.date)
            .where(Bar1d.symbol == "SPY", *day_conds)
            .order_by(Bar1d.date)
            .distinct()
        )
    ]
    n_sessions = len(session_days)

    bucket_col = _session_case()
    per_session_rows = session.execute(
        select(
            LlmCall.session_date,
            bucket_col,
            func.coalesce(func.sum(LlmCall.cost), Decimal(0)),
        )
        .where(where)
        .group_by(LlmCall.session_date, bucket_col)
    ).all()

    # --- Cost per session buckets (item 112) --------------------------
    # At most 31 bars: per session, ISO week, month or quarter. Every
    # bucket in the span gets a row, $0 included; a call's session_date
    # always lands inside the span, so no cost is dropped.
    call_dates = sorted({row[0] for row in per_session_rows})
    span_days = sorted(set(session_days) | set(call_dates))
    unit = bucket_unit(session_days or span_days)
    buckets: dict[str, dict[str, Any]] = {}
    if span_days:
        if unit == "session":
            bucket_dates = span_days
        else:
            bucket_dates = list(
                _iter_bucket_days(span_days[0], span_days[-1], unit)
            )
        for day in bucket_dates:
            key, label, first, last = _bucket_span(day, unit)
            buckets[key] = {
                "label": label,
                "cal_from": first,
                "cal_to": last,
                "sessions": 0,
                "sess_from": None,
                "sess_to": None,
                "live": Decimal(0),
                "shadows": Decimal(0),
                "reviews": Decimal(0),
                "ask": Decimal(0),
            }
        for day in session_days:
            slot = buckets.get(_bucket_span(day, unit)[0])
            if slot is None:
                continue
            slot["sessions"] += 1
            slot["sess_from"] = (
                day if slot["sess_from"] is None else min(slot["sess_from"], day)
            )
            slot["sess_to"] = (
                day if slot["sess_to"] is None else max(slot["sess_to"], day)
            )
        for sess_date, bucket, cost in per_session_rows:
            slot = buckets.get(_bucket_span(sess_date, unit)[0])
            if slot is None:
                continue
            name = str(bucket)
            if name in ("live", "shadows", "reviews", "ask"):
                slot[name] += Decimal(str(cost or 0))

    per_session = [
        {
            "key": key,
            "label": slot["label"],
            "from": (slot["sess_from"] or slot["cal_from"]).isoformat(),
            "to": (slot["sess_to"] or slot["cal_to"]).isoformat(),
            "sessions": slot["sessions"],
            "live": str(slot["live"]),
            "shadows": str(slot["shadows"]),
            "reviews": str(slot["reviews"]),
            "ask": str(slot["ask"]),
        }
        for key, slot in buckets.items()
    ]

    def _bucket_total(slot: dict[str, Any]) -> Decimal:
        return sum(
            (Decimal(str(slot[p])) for p in ("live", "shadows", "reviews", "ask")),
            Decimal(0),
        )

    grand_total = sum((_bucket_total(s) for s in buckets.values()), Decimal(0))
    highest_key = max(buckets, key=lambda k: _bucket_total(buckets[k]), default=None)
    highest_slot = buckets[highest_key] if highest_key is not None else None
    largest_part = (
        max(
            ("live", "shadows", "reviews", "ask"),
            key=lambda p: highest_slot[p],
        )
        if highest_slot is not None and grand_total > 0
        else None
    )
    per_session_summary = {
        "average_per_session": (
            str((grand_total / n_sessions).quantize(Decimal("0.0001")))
            if n_sessions
            else None
        ),
        "highest": (
            {
                "key": highest_key,
                "total": str(_bucket_total(highest_slot)),
                "largest_part": largest_part,
            }
            if highest_slot is not None and grand_total > 0
            else None
        ),
    }

    # --- monthly cap usage (current New York month) ---------------------
    month_start_utc, _ = _utc_bounds(today.replace(day=1), today)
    monthly = {"live": Decimal(0), "shadow": Decimal(0), "review": Decimal(0),
               "ask": Decimal(0)}
    for source, cost in session.execute(
        select(LlmCall.source, func.coalesce(func.sum(LlmCall.cost), Decimal(0)))
        .where(LlmCall.sent_at >= month_start_utc)
        .group_by(LlmCall.source)
    ):
        if str(source) in monthly:
            monthly[str(source)] += Decimal(str(cost or 0))
    bt_month_cost = Decimal(0)
    for (cost_usd,) in session.execute(
        select(Backtest.cost_usd).where(
            func.coalesce(Backtest.finished_at, Backtest.created_at)
            >= month_start_utc,
            Backtest.status.in_(_TERMINAL_STATUSES),
        )
    ):
        bt_month_cost += Decimal(str(cost_usd or 0))

    # --- assemble purposes ----------------------------------------------
    out_purposes: list[dict[str, Any]] = []
    for key in _PURPOSE_ORDER:
        m = purposes[key]
        note: str | None = None
        facts: dict[str, Any] = {}
        cap: dict[str, Any] | None = None
        if key == "live":
            cycles = config.get("schedule", "cycles", default=[]) or []
            note = (
                f"{len(cycles)} cycles a session, held positions first"
                if cycles
                else "held positions first"
            )
            cap = {
                "kind": "per_cycle",
                "max_calls_per_cycle": int(
                    config.get("decision", "max_calls_per_cycle", default=10)
                ),
            }
        elif key == "shadow":
            facts["strategies"] = shadow_strategies
            note = (
                " · ".join(
                    f"{s['name']} · {s['prompt_version'] or '?'} "
                    f"on {s['model'] or '?'}"
                    for s in shadow_strategies
                )
                or None
            )
            cap = _cap_monthly(
                monthly["shadow"],
                config.get("shadows", "monthly_cost_cap_usd", default=5),
            )
        elif key == "weekly":
            weekly_day = str(
                config.get("reviews", "weekly_day", default="saturday")
            )
            note = f"{weekly_day.capitalize()}s"
            cap = _cap_monthly(
                monthly["review"],
                config.get("reviews", "monthly_cost_cap_usd", default=2),
            )
        elif key == "briefing":
            note = "every trading day before the open"
        elif key == "verdict":
            note = (
                f"drafts after "
                f"{config.get('reviews', 'verdict_after_sessions', default=5)} "
                "sessions"
            )
        elif key == "ask":
            facts["questions"] = ask_questions
            if ask_questions:
                facts["cost_per_question"] = str(
                    (m["cost"] / ask_questions).quantize(Decimal("0.0001"))
                )
                facts["calls_per_question"] = round(m["calls"] / ask_questions, 1)
            cap = _cap_monthly(
                monthly["ask"],
                config.get("ask", "monthly_cost_cap_usd", default=15),
            )
        elif key == "backtest":
            facts["run_ids"] = bt_runs
            facts["answers_reused"] = bt_answers_reused
            facts["runs_without_model_usage"] = bt_without_usage
            if bt_runs:
                note = f"runs #{', #'.join(str(i) for i in bt_runs)}"
                if bt_answers_reused:
                    note += f" · {bt_answers_reused:,} answers reused, no cost"
            cap = _cap_monthly(
                bt_month_cost,
                config.get("backtest", "monthly_cost_cap_usd", default=20),
            )
        used_models = sorted(
            name for name, row in model_rows.items() if key in row["purposes"]
        )
        out_purposes.append(
            {
                "key": key,
                "label": _PURPOSE_LABELS[key],
                "models": used_models,
                "note": note,
                "facts": facts,
                "calls": m["calls"],
                "per_session": (
                    None
                    if key == "backtest" or not n_sessions
                    else round(m["calls"] / n_sessions, 1)
                ),
                "tokens_in": m["tokens_in"],
                "tokens_cached_in": m["tokens_cached_in"],
                "cached_share": (
                    round(m["tokens_cached_in"] / m["tokens_in"], 3)
                    if m["tokens_in"]
                    else None
                ),
                "tokens_cache_write_in": m["tokens_cache_write_in"],
                "tokens_out": m["tokens_out"],
                "cost": _money(m["cost"]),
                "cost_unknown": m["cost_unknown"],
                "cache_saved": _money(m["cache_saved"]),
                "cap": cap,
            }
        )

    # ``other`` rows (a source/kind the spec doesn't name) still count in
    # the total — the purpose rows just don't show them.
    total = _empty_metrics()
    for m in list(purposes.values()) + [other]:
        _add_metrics(total, m)
    non_backtest_calls = sum(
        purposes[k]["calls"] for k in _PURPOSE_ORDER if k != "backtest"
    )
    out_purposes.append(
        {
            "key": "total",
            "label": "Total",
            "models": sorted(model_rows),
            "note": None,
            "facts": {},
            "calls": total["calls"],
            "per_session": (
                round(non_backtest_calls / n_sessions, 1) if n_sessions else None
            ),
            "tokens_in": total["tokens_in"],
            "tokens_cached_in": total["tokens_cached_in"],
            "cached_share": (
                round(total["tokens_cached_in"] / total["tokens_in"], 3)
                if total["tokens_in"]
                else None
            ),
            "tokens_cache_write_in": total["tokens_cache_write_in"],
            "tokens_out": total["tokens_out"],
            "cost": _money(total["cost"]),
            "cost_unknown": total["cost_unknown"],
            "cache_saved": _money(total["cache_saved"]),
            "cap": None,
        }
    )

    # --- By model --------------------------------------------------------
    table = models_table(config)
    models_out = []
    for model_id, row in model_rows.items():
        entry = table.get(model_id) or {}
        cost = row["cost"]
        calls = row["calls"]
        models_out.append(
            {
                "id": model_id,
                "provider": (
                    provider_label(model_provider(config, model_id))
                    if model_id != "unknown"
                    else None
                ),
                "input": entry.get("input"),
                "cached_input": entry.get("cached_input"),
                "cache_write": entry.get("cache_write"),
                "output": entry.get("output"),
                "purposes": sorted(p for p in row["purposes"] if p),
                "calls": calls,
                "tokens_in": row["tokens_in"],
                "tokens_cached_in": row["tokens_cached_in"],
                "tokens_cache_write_in": row["tokens_cache_write_in"],
                "tokens_out": row["tokens_out"],
                "cost": _money(cost),
                "cost_unknown": row["cost_unknown"],
                "cache_saved": _money(row["cache_saved"]),
                "per_call": (
                    str((cost / calls).quantize(Decimal("0.000001")))
                    if calls
                    else None
                ),
            }
        )
    models_out.sort(
        key=lambda m: Decimal(m["cost"]) if m["cost"] is not None else Decimal(-1),
        reverse=True,
    )

    answer = _answer_sentence(
        period=period,
        day_from=day_from,
        day_to=day_to,
        total=total,
        purposes=purposes,
        monthly=monthly,
        bt_month_cost=bt_month_cost,
        n_sessions=n_sessions,
        config=config,
    )

    if period == "this_month":
        period_label = f"so far in {_MONTHS[day_to.month - 1]}"
    elif period == "last_month":
        period_label = f"in {_MONTHS[(day_from or day_to).month - 1]}"
    elif period == "3_months":
        period_label = "in the last 3 months"
    else:
        period_label = "so far"

    return {
        "period": {
            "name": period,
            "label": period_label,
            "from": day_from.isoformat() if day_from else None,
            "to": day_to.isoformat(),
            "sessions": n_sessions,
        },
        "purposes": out_purposes,
        "models": models_out,
        "per_session": per_session,
        "per_session_unit": unit,
        "per_session_summary": per_session_summary,
        "answer": answer,
    }


def _answer_sentence(
    *,
    period: str,
    day_from: date | None,
    day_to: date,
    total: dict[str, Any],
    purposes: dict[str, dict[str, Any]],
    monthly: dict[str, Decimal],
    bt_month_cost: Decimal,
    n_sessions: int,
    config: AppConfig,
) -> str:
    """The fixed-template sentence — no model involved (spec → Model costs)."""
    if period == "this_month":
        when = f"so far in {_MONTHS[day_to.month - 1]}"
    elif period == "last_month":
        when = f"in {_MONTHS[(day_from or day_to).month - 1]}"
    elif period == "3_months":
        when = "in the last 3 months"
    else:
        when = "so far"

    calls = total["calls"]
    cost = total["cost"]
    if calls == 0:
        return f"No model calls {when}."
    parts = [f"{_money2(cost)} {when} over {calls:,} calls."]

    # Backtest share of the total.
    bt_cost = purposes["backtest"]["cost"]
    if bt_cost > 0 and cost > 0 and purposes["backtest"]["calls"]:
        pct = int((bt_cost / cost * 100).quantize(Decimal("1")))
        parts.append(f"Backtests are {pct}% of it")

    # Live decisions per session.
    live_cost = purposes["live"]["cost"]
    if purposes["live"]["calls"] and n_sessions:
        parts.append(
            f"live decisions are {_money2(live_cost)}, "
            f"about {_money2(live_cost / n_sessions)} a session"
        )

    # Purpose closest to its monthly cap (live has no monthly cap).
    caps = {
        "Shadows": (monthly["shadow"], "shadows"),
        "Reviews": (monthly["review"], "reviews"),
        "Ask": (monthly["ask"], "ask"),
        "Backtests": (bt_month_cost, "backtest"),
    }
    limits = {
        "Shadows": config.get("shadows", "monthly_cost_cap_usd", default=5),
        "Reviews": config.get("reviews", "monthly_cost_cap_usd", default=2),
        "Ask": config.get("ask", "monthly_cost_cap_usd", default=15),
        "Backtests": config.get(
            "backtest", "monthly_cost_cap_usd", default=20
        ),
    }
    closest: tuple[str, Decimal, Decimal] | None = None
    for name, (used, _key) in caps.items():
        limit = limits.get(name)
        if limit is None:
            continue
        limit_d = Decimal(str(limit))
        if limit_d <= 0:
            continue
        if closest is None or used / limit_d > closest[1] / closest[2]:
            closest = (name, used, limit_d)
    if closest is not None:
        name, used, limit_d = closest
        parts.append(
            f"{name} are the closest to their cap: "
            f"{_money2(used)} of {_fmt_money(limit_d)}"
        )

    # Cache share of all input and what it saved.
    if total["tokens_in"]:
        share = int(
            (
                Decimal(total["tokens_cached_in"]) / total["tokens_in"] * 100
            ).quantize(Decimal("1"))
        )
        parts.append(
            f"The prompt cache served {share}% of all input "
            f"and saved {_money2(total['cache_saved'])}"
        )

    return ". ".join(parts) + "."
