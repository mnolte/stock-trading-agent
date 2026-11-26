"""Item 083 e2e seed: pause sign-in globally for the screenshot spec.

Default: set ``users.global_locked_until`` one hour past SA_SIM_NOW —
the e2e app's frozen clock — and clear ``sign_in_attempts`` so a dirty
lockout window from an earlier spec cannot leak in. ``--cleanup``
lifts the pause and clears the attempts again, so a reused scratch
database does not leak either state into other specs.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

from app.db.base import get_session_maker
from app.db.models import SignInAttempt, User
from sqlalchemy import delete, select


def main() -> None:
    cleanup = "--cleanup" in sys.argv
    now = datetime.fromisoformat(
        os.environ.get("SA_SIM_NOW", "2026-10-15T17:00:00Z").replace("Z", "+00:00")
    )
    with get_session_maker()() as session:
        session.execute(delete(SignInAttempt))
        user = session.execute(select(User)).scalars().first()
        if user is not None:
            user.global_locked_until = (
                None
                if cleanup
                else (now.astimezone(UTC).replace(tzinfo=None) + timedelta(hours=1))
            )
            session.commit()
    print("083 seed", "cleaned" if cleanup else "paused")


if __name__ == "__main__":
    main()
