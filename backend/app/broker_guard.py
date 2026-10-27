"""One-off: set and verify the broker-side safety guardrails.

``python -m app.broker_guard`` — applies ``no_shorting: true`` and
``max_margin_multiplier: 1`` to the Alpaca paper account, then prints
the values read back. The recurring startup check lives in
``app.api.main.ensure_broker_guard`` (docs/SPEC.md, Protective stops).
"""

from __future__ import annotations

import asyncio
import sys

from app.broker.alpaca import AlpacaPaperBroker
from app.config import AppConfig
from app.market.clock import RealClock
from app.secrets_service import SecretsStore

sys.path.insert(0, ".")  # running from backend/ like the other -m modules


async def _main() -> int:
    config = AppConfig()
    store = SecretsStore(config, RealClock())
    key_id, secret = store.alpaca_pair()
    if not key_id or not secret:
        print("no Alpaca keys stored — nothing to set")
        return 1
    broker = AlpacaPaperBroker(key_id, secret, keyed=True)
    written = await broker.set_account_config(
        no_shorting=True, max_margin_multiplier="1"
    )
    if written.error is not None or written.data is None:
        print(f"set failed: {written.error}")
        return 1
    read = await broker.get_account_config()
    if read.error is not None or read.data is None:
        print(f"read-back failed: {read.error}")
        return 1
    cfg = read.data
    print("account configuration read back:")
    for name, value in cfg.items():
        print(f"  {name}: {value}")
    ok = cfg.get("no_shorting") is True and str(
        cfg.get("max_margin_multiplier")
    ) in ("1", "1.0")
    print("guard confirmed" if ok else "GUARD NOT CONFIRMED — check above")
    return 0 if ok else 1


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
