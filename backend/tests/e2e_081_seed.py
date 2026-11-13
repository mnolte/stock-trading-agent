"""Item 081 e2e seed: the stored_text_scrub event behind the Needs you
item, for the 081 screenshot spec. ``--cleanup`` removes it again so a
reused scratch database does not leak the item into other specs."""

from __future__ import annotations

import sys

from app.db.base import get_session_maker
from app.db.models import Event
from app.engine.state import record_event
from app.market.clock import RealClock
from sqlalchemy import delete, select


def main() -> None:
    cleanup = "--cleanup" in sys.argv
    clock = RealClock()
    with get_session_maker()() as session:
        if cleanup:
            session.execute(
                delete(Event).where(Event.event_type == "stored_text_scrub")
            )
            session.commit()
            print("081 seed cleaned")
            return
        exists = session.execute(
            select(Event.id)
            .where(Event.event_type == "stored_text_scrub")
            .limit(1)
        ).first()
        if exists is None:
            record_event(
                session,
                clock,
                level="error",
                event_type="stored_text_scrub",
                details={"tables": {"cycles": 2, "notifications": 1}},
            )
            session.commit()
        print("081 seed ready")


if __name__ == "__main__":
    main()
