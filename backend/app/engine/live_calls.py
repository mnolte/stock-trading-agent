"""Live decisions failing (docs/SPEC.md, Decision layer).

After each live cycle the runner hands the model responses to
:meth:`LiveModelHealth.settle_cycle`. An episode opens when every live
call of the cycle failed (at least one was made), or any live call got
an authentication error (401 or 403): a critical event (which carries
the critical notification once per episode) and a banner until the
first successful live call clears it. An auth error also marks that
provider's key as failing in Settings → API keys, so Resume checks see
it; a later good call un-marks a key this component marked. Shadows,
reviews and backtests never see this object.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.orm import Session

from app.engine.state import ExecutionState, record_event
from app.llm.base import ModelResponse
from app.llm.models import model_provider
from app.logging_config import ev
from app.market.clock import Clock

if TYPE_CHECKING:
    from app.config import AppConfig

logger = logging.getLogger(__name__)

AUTH_STATUSES = (401, 403)


class KeyHealth(Protocol):
    """The slice of SecretsStore the episode needs (fakes in tests)."""

    def record_test(self, name: str, ok: bool, detail: str | None = None) -> None:
        ...

    def group_status(self, group: str) -> dict[str, object]:
        ...


class LiveModelHealth:
    """Tracks the live-decisions-failing episode for one app."""

    def __init__(
        self,
        *,
        state: ExecutionState,
        clock: Clock,
        config: AppConfig,
        session: Session | None = None,
        secrets: KeyHealth | None = None,
        notify: Callable[[set[str]], None] | None = None,
    ) -> None:
        self._state = state
        self._clock = clock
        self._config = config
        self._secrets = secrets
        self._notify = notify or (lambda topics: None)

    def settle_cycle(
        self, session: Session, calls: list[ModelResponse]
    ) -> None:
        """Evaluate one live cycle's real model calls (cache hits excluded)."""
        if not calls:
            return
        auth = next(
            (c for c in calls if c.http_status in AUTH_STATUSES), None
        )
        succeeded = any(
            c.error is None and not c.refused for c in calls
        )
        if auth is not None:
            self._fail(
                session,
                provider=auth.provider or self._configured_provider(),
                error=auth.error or f"HTTP {auth.http_status}",
                auth_error=True,
            )
        elif not succeeded:
            first = calls[0]
            self._fail(
                session,
                provider=first.provider or self._configured_provider(),
                error=first.error or "refusal",
                auth_error=False,
            )
        elif self._state.live_calls_failing_since is not None:
            self._clear()

    def _configured_provider(self) -> str:
        return model_provider(
            self._config,
            str(self._config.get("decision", "model", default="")),
        )

    def _fail(
        self,
        session: Session,
        *,
        provider: str,
        error: str,
        auth_error: bool,
    ) -> None:
        state = self._state
        state.live_calls_failing_provider = provider
        state.live_calls_failing_error = error
        if auth_error and self._secrets is not None:
            # A 401/403 means the stored key is bad: Settings → API keys
            # shows it failing, and Resume checks refuse on it.
            self._secrets.record_test(
                provider,
                False,
                detail=f"live call returned {error}"[:200],
            )
            state.live_calls_key_marked = provider
        if state.live_calls_failing_since is not None:
            # Episode already open: same banner, no second notification.
            return
        state.live_calls_failing_since = self._clock.now()
        record_event(
            session,
            self._clock,
            level="critical",
            event_type="live_decisions_failing",
            details={
                "provider": provider,
                "error": error,
                "auth_error": auth_error,
                "message": "Live decisions failing",
            },
        )
        ev(
            logger, logging.CRITICAL, "cycle.live_decisions_failing",
            f"live decisions failing: {provider} {error}",
            provider=provider,
        )
        self._notify({"banners", "header"})

    def _clear(self) -> None:
        state = self._state
        marked = state.live_calls_key_marked
        state.live_calls_failing_since = None
        state.live_calls_failing_provider = None
        state.live_calls_failing_error = None
        state.live_calls_key_marked = None
        if marked is not None and self._secrets is not None:
            status: Any = self._secrets.group_status(marked).get("status")
            if status == "failing":
                self._secrets.record_test(
                    marked, True, detail="live call succeeded"
                )
        ev(
            logger, logging.INFO, "cycle.live_decisions_recovered",
            "live decisions recovered — model call succeeded",
        )
        self._notify({"banners", "header"})
