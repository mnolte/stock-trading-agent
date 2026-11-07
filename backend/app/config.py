from __future__ import annotations

import copy
import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


def _resolve_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str(ROOT / path)


class EnvSettings(BaseSettings):
    """Values that come from .env or the environment.

    API keys are not read here: they live encrypted in the secrets table
    and are entered in Settings. Keys still present in .env are imported
    once at startup and then ignored (docs/SPEC.md, API keys).
    """

    model_config = SettingsConfigDict(
        env_file=os.environ.get("SA_ENV_FILE") or str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: str = Field(default="paper", alias="MODE")
    sa_sim_now: str = Field(default="", alias="SA_SIM_NOW")
    sa_host: str = Field(default="127.0.0.1", alias="SA_HOST")
    sa_port: int = Field(default=8000, alias="SA_PORT")
    sa_data_dir: str = Field(default="./data", alias="SA_DATA_DIR")
    sa_db_path: str = Field(default="./data/stock-agent.db", alias="SA_DB_PATH")
    # Which agent copy this is on a shared paper account — stamped into
    # every client order id (PR 23); empty until instances exist.
    sa_instance_id: str = Field(default="", alias="SA_INSTANCE_ID")
    # Sign-in (PR 23): "off" (loopback only) or "required". Off on any
    # other bind address refuses startup (hard rule 10). Parsed once,
    # fail-closed: anything else is refused at settings load.
    sa_auth: Literal["off", "required"] = Field(default="off", alias="SA_AUTH")
    # The public base URL Caddy serves (VPS); used for cookie/WebSocket
    # origin checks. Locally the bind address is the origin.
    sa_public_url: str = Field(default="", alias="SA_PUBLIC_URL")
    # Comma-separated addresses whose X-Forwarded-For is trusted.
    sa_trusted_proxy: str = Field(default="", alias="SA_TRUSTED_PROXY")

    @field_validator("sa_data_dir", "sa_db_path", mode="before")
    @classmethod
    def _resolve_relative_path(cls, value: str) -> str:
        return _resolve_path(value)

    @field_validator("sa_auth", mode="before")
    @classmethod
    def _normalise_auth(cls, value: object) -> object:
        """Whitespace and case are not significant: ' Required ' = required."""
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("sa_instance_id", mode="before")
    @classmethod
    def _safe_instance_id(cls, value: object) -> object:
        """An instance named like an order kind (entry|stop|target|exit|
        breach) would break client-order-id parsing: our own ids would
        read as the pre-instance format and exits would misattribute."""
        from app.broker.client_id import KINDS, safe_instance

        if isinstance(value, str) and safe_instance(value).lower() in KINDS:
            raise ValueError(
                f"SA_INSTANCE_ID {value!r} collides with an order kind"
            )
        return value


def _leaf_paths(tree: dict[str, Any], prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Every leaf path in ``tree`` — a dict recurses, anything else is a leaf."""
    paths: list[tuple[str, ...]] = []
    for key, value in tree.items():
        if isinstance(value, dict):
            paths.extend(_leaf_paths(value, prefix + (key,)))
        else:
            paths.append(prefix + (key,))
    return paths


def _get(tree: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = tree
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _filter_known(
    base: dict[str, Any], override: dict[str, Any]
) -> tuple[dict[str, Any], list[tuple[str, ...]]]:
    """``override`` without keys ``base`` doesn't have; returns (kept, dropped).

    A dict unknown at a level is dropped whole: every leaf under it is
    unknown. ``models`` stays open-ended — a local install may add a
    model the example doesn't ship yet.
    """
    dropped: list[tuple[str, ...]] = []

    def walk(base_node: dict[str, Any], over_node: dict[str, Any], prefix: tuple[str, ...]) -> dict[str, Any]:
        kept: dict[str, Any] = {}
        for key, value in over_node.items():
            if prefix == () and key == "models":
                kept[key] = value
                continue
            known = base_node.get(key)
            if key not in base_node:
                dropped.extend(_leaf_paths({key: value}, prefix))
                continue
            if isinstance(known, dict) and isinstance(value, dict):
                kept[key] = walk(known, value, prefix + (key,))
            else:
                kept[key] = value
        return kept

    return walk(base, override, ()), dropped


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` onto ``base``.

    Every level is rebuilt: the result shares no dict or list with either
    input, so pending and active trees can never corrupt each other.
    """
    merged: dict[str, Any] = {}
    for key, value in base.items():
        over = override.get(key)
        if isinstance(value, dict):
            merged[key] = _deep_merge(
                value, over if isinstance(over, dict) else {}
            )
        else:
            merged[key] = (
                copy.deepcopy(over) if key in override else copy.deepcopy(value)
            )
    for key, value in override.items():
        if key not in base:
            merged[key] = copy.deepcopy(value)
    return merged


class AppConfig:
    """Unified config: .env values plus the YAML config file.

    Settings saved through the GUI are stored as rows in the
    ``settings_versions`` table and applied on top of the YAML. A save
    becomes *pending* and moves to *active* at the start of the next
    decision cycle (docs/SPEC.md, Settings).
    """

    def __init__(self) -> None:
        self.env = EnvSettings()
        # Layer 1: the example always loads — every default reaches every
        # install. Layer 2: an optional local config.yaml overrides it key
        # by key. Unknown keys are warned once and ignored (docs/SPEC.md,
        # Config layers).
        self._example = self._read_first(
            ROOT / "backend" / "config.example.yaml",
            ROOT / "config.example.yaml",
        )
        self._local_path: Path | None = None
        self._local: dict[str, Any] = {}
        for path in (ROOT / "backend" / "config.yaml", ROOT / "config.yaml"):
            if path.exists():
                self._local_path = path
                self._local = _read_yaml(path)
                break
        self._warned_unknown: set[str] = set()
        local_kept, unknown = _filter_known(self._example, self._local)
        self._local_override_count = len(_leaf_paths(local_kept))
        self._unknown_keys: set[str] = set()
        for path in unknown:
            self._warn_unknown(path, "config.yaml")
        self._yaml = _deep_merge(self._example, local_kept)
        # The settings in force right now (YAML + last promoted version).
        self._active: dict[str, Any] = self._yaml
        # Saved-but-not-yet-promoted settings (YAML + pending version).
        self._pending: dict[str, Any] | None = None
        self.active_version: int | None = None
        self.pending_version: int | None = None
        # Set by the API when saved settings move cycle times.
        self.reschedule_cycles: bool = False

    @staticmethod
    def _read_first(*candidates: Path) -> dict[str, Any]:
        for path in candidates:
            if path.exists():
                return _read_yaml(path)
        return {}

    def _warn_unknown(self, path: tuple[str, ...], source: str) -> None:
        """WARN once per unknown key; it is ignored (docs/SPEC.md)."""
        dotted = ".".join(path)
        if dotted in self._warned_unknown:
            return
        self._warned_unknown.add(dotted)
        self._unknown_keys.add(dotted)
        logging.getLogger(__name__).warning(
            "config key %s in %s is not in config.example.yaml — ignored",
            dotted, source,
        )

    def _known_only(self, tree: dict[str, Any], source: str) -> dict[str, Any]:
        kept, unknown = _filter_known(self._example, tree)
        for path in unknown:
            self._warn_unknown(path, source)
        return kept

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self._active
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def get_int(self, *keys: str, default: int) -> int:
        """``get`` as int; a malformed value falls back to ``default``.

        A bad saved setting must never crash the cycle: the threshold
        falls back and the failure is logged (hard rule 15).
        """
        raw = self.get(*keys, default=default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            logging.getLogger(__name__).warning(
                "config %s = %r is not an int; using %s",
                ".".join(keys), raw, default,
            )
            return default

    def get_decimal(
        self, *keys: str, default: Decimal | int | float | str
    ) -> Decimal:
        """``get`` as Decimal; a malformed value falls back to ``default``."""
        raw = self.get(*keys, default=None)
        if raw is None:
            return Decimal(str(default))
        try:
            return Decimal(str(raw))
        except ArithmeticError:
            logging.getLogger(__name__).warning(
                "config %s = %r is not a number; using %s",
                ".".join(keys), raw, default,
            )
            return Decimal(str(default))

    # ------------------------------------------------------------------
    # versioned settings

    def effective(self) -> dict[str, Any]:
        """A deep copy of the active settings tree."""
        return _deep_merge(self._active, {})

    def pending(self) -> dict[str, Any] | None:
        """A deep copy of the pending tree, or None when nothing is saved."""
        if self._pending is None:
            return None
        return _deep_merge(self._pending, {})

    def apply_saved(self, settings: dict[str, Any], version: int) -> None:
        """Make a stored settings version active (startup / promotion)."""
        merged = _deep_merge(self._yaml, self._known_only(settings, "settings"))
        self._active = merged
        self.active_version = version

    def set_pending(self, settings: dict[str, Any], version: int) -> None:
        """Store a newly saved version; it applies at the next cycle."""
        self._pending = _deep_merge(
            self._yaml, self._known_only(settings, "settings")
        )
        self.pending_version = version

    def layer_counts(self) -> dict[str, int]:
        """Settings → Server: "Config: n local overrides · n unknown keys"."""
        return {
            "local_overrides": self._local_override_count,
            "unknown_keys": len(self._unknown_keys),
        }

    def promote_pending(self) -> int | None:
        """Promote the pending version to active; returns its version."""
        if self._pending is None:
            return None
        self._active = self._pending
        self._pending = None
        self.active_version = self.pending_version
        self.pending_version = None
        return self.active_version

    def overlay(self, overrides: dict[str, Any]) -> AppConfig:
        """A read-only view of the active settings with ``overrides`` merged
        on top — how a shadow strategy runs the live functions with its own
        rule and check values (docs/SPEC.md, Strategies and shadows)."""
        view = copy.copy(self)
        view._active = _deep_merge(self._active, overrides)
        view._pending = None
        return view

    @property
    def yaml(self) -> dict[str, Any]:
        return self._yaml


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
    return _config
