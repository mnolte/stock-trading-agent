"""Seed shape lint (issue #122): the e2e and README seeds write rows the
live engine could have written.

PR #116 fixed two drifts by hand — a decision on a 16:46 cycle that is
not on the schedule, and a retired ``day trades`` gate. This runs both
seeds into the per-test database and checks every seeded cycle sits on
``schedule.cycles`` (New York time) and every gate name is one the live
gate code (app/risk/gates.py) can produce.
"""

from __future__ import annotations

import re
from datetime import UTC
from pathlib import Path
from typing import Any

from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision
from app.market.clock import NY_TZ
from sqlalchemy import select

from tests import e2e_seed, readme_seed

GATES_PY = Path(__file__).resolve().parents[1] / "app" / "risk" / "gates.py"
MATH_PY = Path(__file__).resolve().parents[1] / "app" / "risk" / "math.py"


def _live_gate_names() -> set[str]:
    """Every check name the live gate code writes: ``block(…)``/``ok(…)``
    calls and explicit ``GateCheck(…)`` rows."""
    text = GATES_PY.read_text()
    names = set(re.findall(r"\b(?:block|ok)\(\s*\"([a-z_]+)\"", text))
    names |= set(re.findall(r"GateCheck\(\s*\"([a-z_]+)\"", text))
    return names


def _sizing_limits() -> set[str]:
    """The sizing caps a ``position_size`` block reports in blocked_by and
    sizing.limited_by (app/risk/math.py position_size)."""
    text = MATH_PY.read_text()
    return set(re.findall(r"\(\"([a-z_]+)\", shares_for\(", text)) | {"stop_distance"}


def test_live_gate_names_are_found() -> None:
    """The source scan itself works — an empty set would pass anything."""
    names = _live_gate_names()
    assert {"kill_switch", "max_open_positions", "position_size", "order_sanity"} <= names
    assert "day_trades" not in names
    assert {"risk_per_trade", "max_position", "open_risk", "cash"} <= _sizing_limits()


def test_seed_rows_land_on_cycles_and_use_live_gates() -> None:
    e2e_seed.main()
    readme_seed.main()

    configured = {str(c) for c in get_config().get("schedule", "cycles")}
    gates = _live_gate_names()
    limits = _sizing_limits()
    with get_session_maker()() as session:
        cycles = list(
            session.execute(select(Cycle).where(Cycle.kind == "cycle")).scalars()
        )
        decisions = list(session.execute(select(Decision)).scalars())

    assert cycles, "the seeds wrote no cycles"
    off_schedule = []
    for cycle in cycles:
        ny = cycle.scheduled_time.replace(tzinfo=UTC).astimezone(NY_TZ)
        if ny.strftime("%H:%M") not in configured or ny.second or ny.microsecond:
            off_schedule.append(f"cycle {cycle.id}: {ny.isoformat()}")
    assert not off_schedule, "seeded cycles off schedule.cycles:\n" + "\n".join(off_schedule)

    seeded_gates = [d for d in decisions if d.gates_result and d.gates_result.get("checks")]
    assert seeded_gates, "the seeds wrote no gate checks"
    bad: list[str] = []
    for decision in decisions:
        result: dict[str, Any] = decision.gates_result or {}
        for check in result.get("checks") or []:
            if check["name"] not in gates:
                bad.append(f"decision {decision.id}: check {check['name']!r}")
        blocked_by = result.get("blocked_by")
        if blocked_by is not None and blocked_by not in gates | limits:
            bad.append(f"decision {decision.id}: blocked_by {blocked_by!r}")
        sizing: dict[str, Any] = result.get("sizing") or {}
        for limit in sizing.get("limited_by") or []:
            if limit not in limits:
                bad.append(f"decision {decision.id}: limited_by {limit!r}")
        # The snapshot's own clock agrees with its cycle.
        time_ny = (decision.input_snapshot or {}).get("time_ny")
        if time_ny is not None:
            cycle = next(c for c in cycles if c.id == decision.cycle_id)
            ny = cycle.scheduled_time.replace(tzinfo=UTC).astimezone(NY_TZ)
            if not str(time_ny).startswith(ny.strftime("%Y-%m-%dT%H:%M")):
                bad.append(f"decision {decision.id}: time_ny {time_ny} vs cycle {ny}")
    assert not bad, "seed rows the live engine can't write:\n" + "\n".join(bad)
