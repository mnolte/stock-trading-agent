"""The one way to start the app: ``python -m app.serve``.

Reads ``SA_HOST`` and ``SA_PORT`` and starts Uvicorn with them — no
Makefile target, script or Compose file passes ``--host`` or ``--port``
(docs/SPEC.md, Security hardening → One source for the address). The
address actually bound is logged at startup. ``SA_RELOAD=1`` turns on
Uvicorn's reloader for ``make dev``.
"""

from __future__ import annotations

import logging
import os

from app.config import ROOT


def main() -> None:
    # umask 077 before anything can create a file (docs/SPEC.md, Security
    # hardening → Files on disk); the lifespan enforces the data-dir
    # modes as well.
    os.umask(0o077)
    from app.config import get_config

    config = get_config()
    host = config.env.sa_host.strip() or "127.0.0.1"
    port = int(config.env.sa_port)
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("app").info(
        "starting on %s:%d (SA_HOST/SA_PORT)", host, port
    )

    import uvicorn

    # proxy_headers=False: the app decides itself whose X-Forwarded-*
    # headers are trusted (SA_TRUSTED_PROXY in app.auth.guard) — uvicorn
    # believing them for any peer would let anyone spoof the client
    # address and the scheme.
    uvicorn.run(
        "app.api.main:app",
        host=host,
        port=port,
        workers=1,
        proxy_headers=False,
        reload=os.environ.get("SA_RELOAD") == "1",
        reload_dirs=[str(ROOT / "backend")],
    )


if __name__ == "__main__":
    main()
