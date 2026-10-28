"""PR 24: ``python -m app.serve`` is the only way to start the app — it
reads SA_HOST/SA_PORT; nothing anywhere may pass --host/--port (docs/
SPEC.md, Security hardening → One source for the address)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Files checked for --host/--port (the Makefile is checked too, even
# though the spec only demands it from PR 28 — it is already clean).
_SCANNED = [
    ROOT / "Dockerfile",
    ROOT / "Makefile",
    *sorted(ROOT.glob("docker-compose*.yml")),
    *sorted((ROOT / "deploy").glob("*.sh")),
]


def test_serve_reads_host_port_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeUvicorn:
        @staticmethod
        def run(app: str, **kwargs: object) -> None:
            captured["app"] = app
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn)
    monkeypatch.setenv("SA_HOST", "0.0.0.0")
    monkeypatch.setenv("SA_PORT", "8123")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_config", None)
    from app.serve import main

    main()
    assert captured["app"] == "app.api.main:app"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8123
    assert captured["workers"] == 1
    # uvicorn must not trust X-Forwarded-* from arbitrary peers: the app
    # applies SA_TRUSTED_PROXY itself.
    assert captured["proxy_headers"] is False


def test_no_host_port_flags_anywhere() -> None:
    offenders: list[str] = []
    for path in _SCANNED:
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"--(host|port)\b", line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "--host/--port found:\n" + "\n".join(offenders)


def test_entrypoint_is_app_serve() -> None:
    text = (ROOT / "Dockerfile").read_text()
    assert 'ENTRYPOINT ["python", "-m", "app.serve"]' in text
    assert "uvicorn" not in re.search(r"ENTRYPOINT.*", text).group(0)  # type: ignore[union-attr]


def test_healthz_returns_version_only(client) -> None:  # type: ignore[no-untyped-def]
    data = client.get("/healthz").json()
    assert set(data) == {"version"}
