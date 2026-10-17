"""Logging configuration with rotation (docs/SPEC.md, Build plan, PR 8, Hardening).

Logs are written in the app timezone (Europe/Amsterdam per spec) to a daily
rotated file in the data directory; 30 days of rotated logs are kept.
"""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_config

LOG_RETENTION_DAYS = 30


class _AppTimeFormatter(logging.Formatter):
    """Formats log timestamps in the app timezone (Amsterdam per spec)."""

    def __init__(self, fmt: str, tz: ZoneInfo) -> None:
        super().__init__(fmt)
        self._tz = tz

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=self._tz)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def setup_logging() -> None:
    """Configure rotating file logs in the data directory."""
    cfg = get_config()
    app_tz = ZoneInfo(str(cfg.get("app", "timezone", default="Europe/Amsterdam")))
    log_dir = Path(cfg.env.sa_data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "stock-agent.log"
    handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(
        _AppTimeFormatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            tz=app_tz,
        )
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    # Reduce noisy third-party loggers
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
