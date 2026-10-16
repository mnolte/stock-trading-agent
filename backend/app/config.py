import copy
from pathlib import Path
from typing import Any

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
    """Values that come from .env or the environment."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_secret_key: str = Field(default="", alias="ALPACA_SECRET_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    finnhub_api_key: str = Field(default="", alias="FINNHUB_API_KEY")
    mode: str = Field(default="paper", alias="MODE")
    sa_sim_now: str = Field(default="", alias="SA_SIM_NOW")
    sa_host: str = Field(default="127.0.0.1", alias="SA_HOST")
    sa_port: int = Field(default=8000, alias="SA_PORT")
    sa_data_dir: str = Field(default="./data", alias="SA_DATA_DIR")
    sa_db_path: str = Field(default="./data/stock-agent.db", alias="SA_DB_PATH")

    @field_validator("sa_data_dir", "sa_db_path", mode="before")
    @classmethod
    def _resolve_relative_path(cls, value: str) -> str:
        return _resolve_path(value)


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
        self._yaml = self._load_yaml()
        # The settings in force right now (YAML + last promoted version).
        self._active: dict[str, Any] = self._yaml
        # Saved-but-not-yet-promoted settings (YAML + pending version).
        self._pending: dict[str, Any] | None = None
        self.active_version: int | None = None
        self.pending_version: int | None = None
        # Set by the API when saved settings move cycle times.
        self.reschedule_cycles: bool = False

    def _load_yaml(self) -> dict[str, Any]:
        candidates = (
            ROOT / "backend" / "config.yaml",
            ROOT / "config.yaml",
            ROOT / "backend" / "config.example.yaml",
            ROOT / "config.example.yaml",
        )
        for path in candidates:
            if path.exists():
                with path.open("r", encoding="utf-8") as fh:
                    return yaml.safe_load(fh) or {}
        return {}

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self._active
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    # ------------------------------------------------------------------
    # versioned settings

    def effective(self) -> dict[str, Any]:
        """A deep copy of the active settings tree."""
        return _deep_merge(self._active, {})

    def apply_saved(self, settings: dict[str, Any], version: int) -> None:
        """Make a stored settings version active (startup / promotion)."""
        self._active = _deep_merge(self._yaml, settings)
        self.active_version = version

    def set_pending(self, settings: dict[str, Any], version: int) -> None:
        """Store a newly saved version; it applies at the next cycle."""
        self._pending = _deep_merge(self._yaml, settings)
        self.pending_version = version

    def promote_pending(self) -> int | None:
        """Promote the pending version to active; returns its version."""
        if self._pending is None:
            return None
        self._active = self._pending
        self._pending = None
        self.active_version = self.pending_version
        self.pending_version = None
        return self.active_version

    @property
    def yaml(self) -> dict[str, Any]:
        return self._yaml


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
    return _config
