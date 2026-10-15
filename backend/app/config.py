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
    sa_host: str = Field(default="127.0.0.1", alias="SA_HOST")
    sa_port: int = Field(default=8000, alias="SA_PORT")
    sa_data_dir: str = Field(default="./data", alias="SA_DATA_DIR")
    sa_db_path: str = Field(default="./data/stock-agent.db", alias="SA_DB_PATH")

    @field_validator("sa_data_dir", "sa_db_path", mode="before")
    @classmethod
    def _resolve_relative_path(cls, value: str) -> str:
        return _resolve_path(value)


class AppConfig:
    """Unified config: .env values plus the YAML config file."""

    def __init__(self) -> None:
        self.env = EnvSettings()
        self._yaml = self._load_yaml()

    def _load_yaml(self) -> dict[str, Any]:
        for name in ("config.yaml", "config.example.yaml"):
            path = ROOT / name
            if path.exists():
                with path.open("r", encoding="utf-8") as fh:
                    return yaml.safe_load(fh) or {}
        return {}

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self._yaml
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    @property
    def yaml(self) -> dict[str, Any]:
        return self._yaml


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
    return _config
