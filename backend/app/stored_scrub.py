"""One-time scrub of stored rows (item 081, spec → Audit of 11 November).

Before the fix, exception text carrying a key could be persisted in
``cycles.data_source_health``, ``notifications.subject``/``body``,
``events.details`` and the Ask message columns. This runs once at
startup — after migrations and after the secret providers are
registered, so ``_all_secrets()`` sees every stored key — and rewrites
those columns through the same patterns and values the log scrubber
uses. The ``stored_text_scrub`` event row is the flag: its presence
means the scrub already ran.

Transactions stay short (hard rule 13): rows are read in batches of
500 and committed per batch; there is no session-hours restriction —
these are writes on old rows only. When any row changed, an error-level
flag event feeds the Needs you item "A key appeared in stored error
text. Replace the FRED and Finnhub keys" (docs/SPEC.md, Needs you).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AskMessage, Cycle, Event, Notification
from app.market.clock import Clock

logger = logging.getLogger(__name__)

EVENT_TYPE = "stored_text_scrub"
_BATCH = 500


def _scrub_batches(
    session: Session,
    model: type[Any],
    columns: list[str],
) -> int:
    """Scrub ``columns`` on every row of ``model``; returns changed rows.

    Ids are read first, then rows are loaded and committed in batches of
    ``_BATCH`` so no transaction stays open across the whole table.
    """
    from app.logging_config import scrub_value

    id_column = model.id
    non_null = [getattr(model, name) for name in columns]
    ids = list(
        session.execute(
            select(id_column)
            .where(or_(*[col.is_not(None) for col in non_null]))
            .order_by(id_column)
        ).scalars()
    )
    changed = 0
    for start in range(0, len(ids), _BATCH):
        chunk = ids[start : start + _BATCH]
        for row in session.scalars(
            select(model).where(id_column.in_(chunk))
        ):
            dirty = False
            for name in columns:
                value = getattr(row, name)
                if value is None:
                    continue
                clean = scrub_value(value)
                if clean != value:
                    setattr(row, name, clean)
                    dirty = True
            if dirty:
                changed += 1
        session.commit()
    return changed


def run(
    session_factory: sessionmaker[Session], clock: Clock
) -> dict[str, int]:
    """Scrub the stored rows once; returns changed-row counts per table.

    Empty dict means the flag row already exists — nothing to do.
    """
    from app.engine.state import record_event

    counts: dict[str, int] = {}
    with session_factory() as session:
        flag = session.execute(
            select(Event.id)
            .where(Event.event_type == EVENT_TYPE)
            .limit(1)
        ).first()
        if flag is not None:
            return {}
        counts["cycles"] = _scrub_batches(
            session, Cycle, ["data_source_health"]
        )
        counts["notifications"] = _scrub_batches(
            session, Notification, ["subject", "body"]
        )
        counts["events"] = _scrub_batches(session, Event, ["details"])
        counts["ask_messages"] = _scrub_batches(
            session,
            AskMessage,
            [
                "text",
                "error",
                "context",
                "steps",
                "references",
                "unverified_numbers",
                "checked_numbers",
            ],
        )
        total = sum(counts.values())
        record_event(
            session,
            clock,
            level="error" if total else "info",
            event_type=EVENT_TYPE,
            details={"tables": counts},
        )
        session.commit()
    logger.info("stored text scrub changed rows: %s", counts)
    return counts


HEARTBEAT_EVENT = "heartbeat_secret_migration"


def migrate_heartbeat(
    session_factory: sessionmaker[Session],
    config: Any,
    secrets: Any,
    clock: Clock,
) -> bool:
    """Move ``ops.heartbeat_url`` into secrets once; blank stored versions.

    Item 081: the dead-man's-switch URL is a write-only secret. A value
    still sitting in config (or an old settings version) is imported
    into the secrets store on the first boot after the change, then
    ignored — the flag event row means later boots never re-import, so
    removing the secret in Settings stays removed. Every stored
    ``settings_versions`` row gets ``ops.heartbeat_url`` blanked so the
    URL never appears in History.
    """
    from app.db.models import SettingsVersion
    from app.engine.state import record_event

    with session_factory() as session:
        flag = session.execute(
            select(Event.id)
            .where(Event.event_type == HEARTBEAT_EVENT)
            .limit(1)
        ).first()
        if flag is not None:
            return False

    url = str(config.get("ops", "heartbeat_url", default="") or "").strip()
    imported = False
    if url and secrets.get("heartbeat_url") is None:
        from app.ops.urls import validate_public_https_url

        try:
            clean = validate_public_https_url(url)
        except Exception:  # noqa: BLE001 - DNS or a bad stored value
            clean = ""
        if clean:
            secrets.save("heartbeat_url", clean)
            imported = True
            logger.warning(
                "imported ops.heartbeat_url into the secrets store; the "
                "config value is ignored now — remove it from config"
            )
        else:
            logger.warning(
                "ops.heartbeat_url failed validation; not imported — "
                "set it again in Settings → Server"
            )

    blanked = 0
    with session_factory() as session:
        for row in session.scalars(select(SettingsVersion)):
            settings = row.settings
            ops = settings.get("ops") if isinstance(settings, dict) else None
            if isinstance(ops, dict) and ops.get("heartbeat_url"):
                row.settings = {
                    **settings,
                    "ops": {**ops, "heartbeat_url": ""},
                }
                blanked += 1
        record_event(
            session,
            clock,
            level="info",
            event_type=HEARTBEAT_EVENT,
            details={"imported": imported, "versions_blanked": blanked},
        )
        session.commit()
    logger.info(
        "heartbeat secret migration: imported=%s versions_blanked=%s",
        imported,
        blanked,
    )
    return True
