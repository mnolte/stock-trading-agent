"""Every literal ``config.get*`` path resolves in ``config.example.yaml``.

Rule 3 (thresholds live in config): a ``get("a", "b", default=…)`` on an
``AppConfig`` whose ``a.b`` the example doesn't carry falls back to a
code constant the operator can't see — the gap the 046 scripted check
found in seven places (G13–G15). Calls on plain dicts obtained from a
section read (``cfg = config.get("insights"); cfg.get("min_trades")``)
are not ``AppConfig`` reads and aren't collected: ``cfg`` receivers only
count when the call shape can't be a ``dict.get`` — a ``default=``
keyword or three or more string positional arguments.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PATH = ROOT / "backend" / "config.example.yaml"

# Read but intentionally absent from the example:
# - the documented one-release compat reads (the example's comment names them)
# - rules.entry_cycles: strategy-overlay key; absent means every cycle
ALLOWED_MISSING: set[tuple[str, ...]] = {
    ("reviews", "model"),
    ("model_prices",),
    ("backtest", "model_cutoffs"),
    ("rules", "entry_cycles"),
}

# Namespaces whose children are data, not fixed keys (event names, model ids).
OPEN_NAMESPACES: tuple[tuple[str, ...], ...] = (
    ("notifications", "events"),
    ("models",),
)

GET_METHODS = {"get", "get_int", "get_decimal", "get_bool", "get_list", "get_float"}


def _example_nodes() -> set[tuple[str, ...]]:
    tree = yaml.safe_load(EXAMPLE_PATH.read_text())
    nodes: set[tuple[str, ...]] = set()

    def walk(node: dict[str, object], prefix: tuple[str, ...]) -> None:
        for key, value in node.items():
            nodes.add(prefix + (key,))
            if isinstance(value, dict):
                walk(value, prefix + (key,))

    walk(tree, ())
    return nodes


def _receiver_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):  # get_config().get(...)
        func = node.func
        return (
            func.attr
            if isinstance(func, ast.Attribute)
            else getattr(func, "id", "")
        )
    return ""


def _literal_strings(call: ast.Call) -> list[str] | None:
    args: list[str] = []
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            args.append(arg.value)
        else:
            return None  # dynamic — can't check statically
    return args


def _is_config_path(call: ast.Call) -> list[str] | None:
    """The literal key path when ``call`` is an ``AppConfig.get*`` — else None."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr in GET_METHODS):
        return None
    name = _receiver_name(func.value)
    if name in ("config", "_config", "get_config"):
        return _literal_strings(call) or None
    if name == "cfg":  # get_config() alias — or a section dict
        args = _literal_strings(call)
        if args is None or not args:
            return None
        has_default_kw = any(k.arg == "default" for k in call.keywords)
        if has_default_kw or len(args) >= 3:
            return args  # dict.get can't take a third positional
        return None  # cfg.get("k", d): ambiguous, treated as dict.get
    return None


def _collect() -> list[tuple[tuple[str, ...], str, int]]:
    found: list[tuple[tuple[str, ...], str, int]] = []
    for path in sorted(ROOT.glob("backend/app/**/*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                keys = _is_config_path(node)
                if keys:
                    found.append((tuple(keys), str(path.relative_to(ROOT)), node.lineno))
    return found


def test_every_literal_config_path_exists_in_the_example() -> None:
    nodes = _example_nodes()
    missing = []
    for keys, rel, line in _collect():
        if keys in nodes or keys in ALLOWED_MISSING:
            continue
        if any(keys[: len(ns)] == ns for ns in OPEN_NAMESPACES):
            continue
        missing.append(f"{'.'.join(keys)} at {rel}:{line}")
    detail = "\n".join(missing)
    assert not missing, f"config paths absent from config.example.yaml:\n{detail}"
