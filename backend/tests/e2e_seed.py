"""Prepare the shared Playwright database before the e2e server starts.

Test-only (never imported by the app). The e2e server's clock is frozen at
Thursday 15 October 2026; a run that starts at that same moment never has
a weekly review due, so the Reviews e2e had nothing to open. Starting the
e2e run on Monday 5 October makes the Saturday 10 October weekly review
due, and the app's own startup catch-up writes it (docs/SPEC.md, Reviews —
Missed runs).

Usage (from backend/, with the e2e env): ``../.venv/bin/python -m tests.e2e_seed``
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

E2E_RUN_START = datetime(2026, 10, 5, 13, 30)  # Monday 09:30 New York, naive UTC


def main() -> None:
    from app.config import get_config
    from app.db.base import get_session_maker
    from app.db.models import Run

    Path(get_config().env.sa_db_path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    with get_session_maker()() as session:
        first = session.execute(select(Run).order_by(Run.id).limit(1)).scalar_one_or_none()
        if first is None:
            starting = Decimal(
                str(get_config().get("performance", "starting_equity", default=10000))
            )
            session.add(Run(started_at=E2E_RUN_START, starting_equity=starting, spy_close=None))
        elif first.started_at > E2E_RUN_START:
            first.started_at = E2E_RUN_START
        session.commit()


if __name__ == "__main__":
    main()
