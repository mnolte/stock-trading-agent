"""The shadow code can never reach a broker client (docs/SPEC.md, Strategies
and shadows — Virtual trading: "a test fails the build if it can").

Three checks: no module in ``app/shadows`` imports a broker or order path;
importing the whole package in a fresh interpreter loads no broker client;
and the runner the app builds holds no broker or executor at all.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

from app.shadows.runner import ShadowRunner
from fastapi.testclient import TestClient

SHADOWS = Path(__file__).resolve().parents[1] / "app" / "shadows"
FORBIDDEN_PREFIXES = (
    "app.broker",
    "app.engine.executor",
    "app.engine.cycle",
    "app.engine.position_check",
    "app.api",
    "alpaca.trading",
)
# What may never be loaded as a side effect of importing the package.
FORBIDDEN_LOADED = (
    "app.broker.base",
    "app.broker.alpaca",
    "app.broker.fake",
    "app.engine.executor",
    "alpaca.trading",
)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_no_shadow_module_imports_a_broker() -> None:
    offenders = [
        f"{path.name}: {name}"
        for path in sorted(SHADOWS.glob("*.py"))
        for name in _imports(path)
        if name.startswith(FORBIDDEN_PREFIXES)
    ]
    assert offenders == []


def test_importing_shadows_loads_no_broker_client() -> None:
    modules = [f"app.shadows.{p.stem}" for p in SHADOWS.glob("*.py") if p.stem != "__init__"]
    code = (
        "import sys\n"
        + "".join(f"import {m}\n" for m in modules)
        + f"bad = [m for m in sys.modules if m.startswith({FORBIDDEN_LOADED!r})]\n"
        + "print(bad)\n"
        + "sys.exit(1 if bad else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=SHADOWS.parents[1],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_runner_takes_no_broker() -> None:
    params = inspect.signature(ShadowRunner.__init__).parameters
    assert not {"broker", "executor"} & set(params)
    for param in params.values():
        assert "Broker" not in str(param.annotation)
        assert "Executor" not in str(param.annotation)


def test_app_runner_holds_no_broker(client: TestClient) -> None:
    from app.broker.alpaca import AlpacaPaperBroker
    from app.broker.fake import FakeBroker
    from app.engine.executor import Executor

    runner = client.app.state.shadow_runner  # type: ignore[attr-defined]
    seen: set[int] = set()

    def walk(obj: object, depth: int) -> None:
        if id(obj) in seen or depth > 3:
            return
        seen.add(id(obj))
        assert not isinstance(obj, FakeBroker | AlpacaPaperBroker | Executor), obj
        if callable(obj) and getattr(obj, "__closure__", None):
            for cell in obj.__closure__ or ():  # type: ignore[attr-defined]
                walk(cell.cell_contents, depth + 1)
        for value in getattr(obj, "__dict__", {}).values():
            walk(value, depth + 1)

    walk(runner, 0)
