"""Prepare three decision.v3 rows for the PR 30 drawer screenshots.

Test-only scratch data (never imported by the app): two SBUX candidate
rows — one where the room filter removes every option, one where it
does not — and one SBUX held row, all built by the real v3 code on the
stored v2 snapshots under ``tests/fixtures/v3/``.

Usage (from backend/, pointing at the e2e db):
``../.venv/bin/python -m tests.v3_shot_seed``
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from tests.test_decide_v3 import (
    _calendar,
    _fixture,
    _load_bars,
    _sbux_held,
)


def main() -> None:
    from app.config import AppConfig, get_config
    from app.db.base import get_session_maker
    from app.db.models import Cycle, Decision, Run

    Path(get_config().env.sa_db_path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    with get_session_maker()() as session:
        if session.execute(
            select(Decision.id).where(Decision.prompt_version == "decision.v3")
        ).scalar_one_or_none() is not None:
            return
        first = session.execute(select(Run).order_by(Run.id).limit(1)).scalar_one_or_none()
        if first is None:
            session.add(
                Run(
                    started_at=datetime(2026, 10, 5, 13, 0),
                    starting_equity=Decimal("10000"),
                    spy_close=None,
                )
            )
        cycle = Cycle(
            kind="cycle",
            scheduled_time=datetime(2026, 10, 28, 14, 46),
            started_at=datetime(2026, 10, 28, 14, 46),
            finished_at=datetime(2026, 10, 28, 14, 46, 12),
            duration_seconds=12.0,
            status="done",
        )
        session.add(cycle)
        session.flush()
        cycle_id = cycle.id
        session.commit()

    cand = _build_row("sbux_candidate.json", "sbux_candidate_bars.json", "candidate")
    held_build, held_snap = _build_row(
        "sbux_held.json", "sbux_held_bars.json", "held", held=_sbux_held()
    )()

    from app.db.base import get_session_maker

    with get_session_maker()() as session:
        # Row 1: the room filter removes every option — no model call.
        room_build, room_snap = cand()
        room_snap.pop("__config", None)
        session.add(
            Decision(
                cycle_id=cycle_id,
                symbol="SBUX",
                context="candidate",
                input_snapshot=room_snap,
                prompt_version="decision.v3",
                prompt_hash=("ffcc7afcf74f8ee47cd3cad7e28289c0c43997adc14fc9e13477320b3a27abcd"),
                model="gpt-4o-mini",
                rules_result={"status": "pass", "checks": []},
                model_output=None,
                validation_result={
                    "final_action": "no_call",
                    "error": room_build.reason,
                    "note": f"No call — {room_build.reason}",
                },
                gates_result={"blocked_by": None, "checks": []},
                final_action="no_call",
                no_call_reason="no_options",
                levels={"levels": room_build.fields.get("levels")},
                options=room_build.options_column,
                options_version="v3",
            )
        )
        # Row 2: room raised → options offered, model chose D.
        opt_build, opt_snap = cand(
            overlay={"decision": {"v3": {"min_room_atr": 0.3}}}
        )
        chosen = opt_build.fields["options"][-1]
        model_out = {
            "symbol": "SBUX",
            "action": "enter",
            "option_id": chosen["id"],
            "confidence": 0.6,
            "levels_used": [lv for lv in (chosen.get("stop_level"), chosen.get("target_level")) if lv],
            "conflicts": ["October high L1 before the target"],
            "data_flags": [],
            "thesis": (
                "Trends and regime up, and the first hurdle sits far enough away. "
                "The pullback held above the session low; October's high is the "
                "main risk on the way to the target."
            ),
            "news_used": [],
        }
        from app.decide.output import validate_model_output_v3

        v = validate_model_output_v3(
            json.dumps(model_out),
            symbol="SBUX",
            context="candidate",
            snapshot=opt_snap,
            atr_1h=Decimal(str(opt_snap["indicators_1h"]["atr14"])),
            current_stop=None,
            config=opt_snap["__config"],
        )
        opt_snap.pop("__config", None)
        session.add(
            Decision(
                cycle_id=cycle_id,
                symbol="SBUX",
                context="candidate",
                input_snapshot=opt_snap,
                prompt_version="decision.v3",
                prompt_hash=("ffcc7afcf74f8ee47cd3cad7e28289c0c43997adc14fc9e13477320b3a27abcd"),
                model="gpt-4o-mini",
                rules_result={"status": "pass", "checks": []},
                model_output=(
                    v.output.model_dump(mode="json") if v.output else model_out
                ),
                validation_result={
                    "valid": v.valid,
                    "model_action": v.model_action,
                    "final_action": v.final_action,
                    "changed_by_code": v.changed_by_code,
                    "error": v.error,
                    "reason": v.reason,
                    "code_written": v.code_written,
                    "note": v.note,
                    "checks": [
                        {"name": c.name, "passed": c.passed, "detail": c.detail, "value": c.value}
                        for c in v.checks
                    ],
                },
                gates_result={
                    "blocked_by": None,
                    "checks": [
                        {"name": "risk per trade", "passed": True, "detail": "0.34% equity"},
                        {"name": "open risk", "passed": True, "detail": "1.9% of 3%"},
                    ],
                },
                final_action=v.final_action,
                chosen_option=chosen["id"],
                levels={"levels": opt_build.fields.get("levels")},
                options=opt_build.options_column,
                options_version="v3",
                explanation_check=(
                    v.explanation.to_json() if v.explanation is not None else None
                ),
            )
        )
        # Row 3: held — hold, position facts and stop options.
        held_out = {
            "symbol": "SBUX",
            "action": "hold",
            "option_id": None,
            "confidence": 0.55,
            "levels_used": ["L5"],
            "conflicts": ["daily trend mixed", "session high L5 before the target"],
            "data_flags": [],
            "thesis": (
                "The trade is in profit but the only stop on offer would still be "
                "a small loss and sits close to the price. The position is sold "
                "before earnings in the next session anyway, so keeping the "
                "current stop gives it room until then."
            ),
            "news_used": [],
        }
        vh = validate_model_output_v3(
            json.dumps(held_out),
            symbol="SBUX",
            context="held",
            snapshot=held_snap,
            atr_1h=Decimal(str(held_snap["indicators_1h"]["atr14"])),
            current_stop=Decimal("101.74"),
            config=held_snap["__config"],
        )
        held_snap.pop("__config", None)
        session.add(
            Decision(
                cycle_id=cycle_id,
                symbol="SBUX",
                context="held",
                input_snapshot=held_snap,
                prompt_version="decision.v3",
                prompt_hash=("ffcc7afcf74f8ee47cd3cad7e28289c0c43997adc14fc9e13477320b3a27abcd"),
                model="gpt-4o-mini",
                rules_result={"status": "pass", "checks": []},
                model_output=(
                    vh.output.model_dump(mode="json") if vh.output else held_out
                ),
                validation_result={
                    "valid": vh.valid,
                    "model_action": vh.model_action,
                    "final_action": vh.final_action,
                    "changed_by_code": vh.changed_by_code,
                    "error": vh.error,
                    "reason": vh.reason,
                    "code_written": vh.code_written,
                    "note": vh.note,
                    "checks": [
                        {"name": c.name, "passed": c.passed, "detail": c.detail, "value": c.value}
                        for c in vh.checks
                    ],
                },
                gates_result={"blocked_by": None, "checks": []},
                final_action=vh.final_action,
                chosen_option=None,
                levels={"levels": held_build.fields.get("levels")},
                options=held_build.options_column,
                options_version="v3",
                explanation_check=(
                    (vh.explanation.to_json() if vh.explanation is not None else {})
                    | ({"claims": vh.claims.to_json()} if vh.claims is not None else {})
                    or None
                ),
            )
        )
        session.commit()
        print(
            "seeded v3 rows:",
            [
                row[0]
                for row in session.execute(
                    select(Decision.id).where(Decision.prompt_version == "decision.v3")
                ).all()
            ],
        )


def _build_row(fixture: str, bars: str, context: str, held=None):
    """Return a builder: (overlay) -> (V3Build, snapshot-with-config)."""
    from app.config import AppConfig
    from app.decide.v3 import HeldV3, build_v3_fields

    snap = _fixture(fixture)
    b1, daily, m5 = _load_bars(bars)
    days = _calendar(
        *[str(b.date) for b in daily],
        str(daily[-1].date + timedelta(days=1)),
    )

    def run(overlay=None):
        from app.shadows.runner import (
            indicators_from_snapshot,
            regime_from_snapshot,
        )

        config = AppConfig().overlay(overlay or {})
        build = build_v3_fields(
            context=context,
            base=dict(snap),
            price=_dec(snap.get("price")),
            reference=_dec(snap.get("stop_reference_price")),
            atr_1h=_dec((snap.get("indicators_1h") or {}).get("atr14")),
            indicators=indicators_from_snapshot(snap["symbol"], snap),
            regime=regime_from_snapshot(snap, config),
            bars_1h=b1,
            daily=daily,
            today_5m=m5,
            earnings=(date(2026, 10, 29), "after_close"),
            calendar=days,
            config=config,
            cycle_time_ny=datetime.fromisoformat(str(snap["time_ny"])),
            held=held,
        )
        merged = {**snap, **build.fields, "__config": config}
        return build, merged

    return run


def _dec(value):
    return Decimal(str(value)) if value is not None else None


if __name__ == "__main__":
    main()
