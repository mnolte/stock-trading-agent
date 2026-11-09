"""E2E seed for PR 23: create the single account so the Playwright spec
can drive the real sign-in screen. Runs only in the e2e webServer
command when E2E_AUTH=1 — the frozen simulated clock makes the TOTP
deterministic for the spec.

Writes the plaintext TOTP secret to SA_DATA_DIR/e2e-totp-secret so the
spec can compute the current 6-digit code; the file is a test fixture
inside the temp e2e data dir, never a real secret.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from app.auth.create_user import create_or_reset_user
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import SignInAttempt
from app.market.clock import FakeClock
from sqlalchemy import delete

PASSWORD = "e2e-test-password-14c"

now = datetime.fromisoformat(
    os.environ.get("SA_SIM_NOW", "2026-10-15T17:00:00Z").replace("Z", "+00:00")
)
config = get_config()
with get_session_maker()() as session:
    _user, secret, codes = create_or_reset_user(
        session,
        config,
        FakeClock(now.astimezone(UTC)),
        username="marc",
        password=PASSWORD,
    )
    # The frozen clock pins every attempt inside the 15-minute lockout
    # window, so failures recorded by earlier runs (the e2e database is
    # cached between CI jobs) would lock every sign-in forever.
    session.execute(delete(SignInAttempt))
    session.commit()
out = os.path.join(config.env.sa_data_dir, "e2e-totp-secret")
with open(out, "w") as handle:
    handle.write(secret)
# Recovery codes too: the TOTP step can be used once (replay check), so
# the spec's later sign-ins spend one recovery code each.
codes_out = os.path.join(config.env.sa_data_dir, "e2e-recovery-codes")
with open(codes_out, "w") as handle:
    handle.write("\n".join(codes))
print(f"e2e auth user ready (totp secret in {out})")
