"""Config layers (docs/SPEC.md, Config layers; item 050).

The example always loads, a local config.yaml overrides it key by key,
and saved settings go on top. A key the example doesn't have warns once
and is ignored; a configured model missing from the merged list warns
once at startup and is refused at save.
"""

from __future__ import annotations

import logging
from pathlib import Path

import app.config as config_module
import pytest
from app.config import AppConfig
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    monkeypatch.setattr(config_module, "ROOT", tmp_path)
    return AppConfig()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_example_key_reaches_old_config_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key added only to the example is visible with an old config.yaml."""
    _write(
        tmp_path / "backend" / "config.example.yaml",
        "execution:\n  mode: automatic\n  new_flag: 42\n",
    )
    _write(tmp_path / "backend" / "config.yaml", "execution:\n  mode: supervised\n")
    config = _config(tmp_path, monkeypatch)
    assert config.get("execution", "new_flag") == 42
    assert config.get("execution", "mode") == "supervised"
    assert config.layer_counts() == {"local_overrides": 1, "unknown_keys": 0}


def test_unknown_key_warns_once_and_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _write(
        tmp_path / "backend" / "config.example.yaml",
        "risk:\n  drawdown_kill_pct: 8\n",
    )
    _write(
        tmp_path / "backend" / "config.yaml",
        "risk:\n  drawdown_kill_pct: 5\n  pdt_max_day_trades: 3\n",
    )
    with caplog.at_level(logging.WARNING, logger="app.config"):
        config = _config(tmp_path, monkeypatch)
    assert config.get("risk", "drawdown_kill_pct") == 5
    assert config.get("risk", "pdt_max_day_trades") is None
    warns = [r for r in caplog.records if "pdt_max_day_trades" in r.getMessage()]
    assert len(warns) == 1 and warns[0].levelno == logging.WARNING
    # The same key in a settings version warns not again but is ignored.
    caplog.clear()
    config.apply_saved(
        {"risk": {"pdt_max_day_trades": 3, "drawdown_kill_pct": 6}}, 2
    )
    assert config.get("risk", "pdt_max_day_trades") is None
    assert config.get("risk", "drawdown_kill_pct") == 6
    assert not [r for r in caplog.records if "pdt_max_day_trades" in r.getMessage()]
    assert config.layer_counts() == {"local_overrides": 1, "unknown_keys": 1}


def test_local_model_flag_falls_back_to_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gpt-6.1-sol in an old config.yaml still gets supports_temperature
    from the example (the 4 Nov briefing bug)."""
    _write(
        tmp_path / "backend" / "config.example.yaml",
        "models:\n  gpt-6.1-sol:\n"
        "    provider: openai\n    input: 2.0\n    output: 10.0\n"
        "    supports_temperature: false\n",
    )
    _write(
        tmp_path / "backend" / "config.yaml",
        "models:\n  gpt-6.1-sol:\n    provider: openai\n    input: 2.0\n    output: 10.0\n",
    )
    config = _config(tmp_path, monkeypatch)
    assert config.get("models", "gpt-6.1-sol", "supports_temperature") is False


def test_symbol_keyed_maps_keep_unknown_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """universe.sectors and universe.names are open maps (item 052): a
    ticker the example doesn't list is kept at every layer, silently —
    it is user data, not an unknown key."""
    _write(
        tmp_path / "backend" / "config.example.yaml",
        "universe:\n  sectors: {MSFT: Technology}\n  names: {MSFT: Microsoft}\n",
    )
    _write(
        tmp_path / "backend" / "config.yaml",
        "universe:\n  sectors: {TSLA: Consumer Discretionary}\n",
    )
    config = _config(tmp_path, monkeypatch)
    assert config.get("universe", "sectors", "TSLA") == "Consumer Discretionary"
    config.apply_saved(
        {
            "universe": {
                "sectors": {"ACN": "Technology"},
                "names": {"ACN": "Accenture"},
            }
        },
        2,
    )
    assert config.get("universe", "sectors", "ACN") == "Technology"
    assert config.get("universe", "names", "ACN") == "Accenture"
    assert config.get("universe", "sectors", "TSLA") == "Consumer Discretionary"
    assert config.layer_counts()["unknown_keys"] == 0


def test_open_maps_do_not_leak_to_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Only the listed maps are open: a misspelt sibling and a retired
    key are still dropped and warned once (item 052)."""
    _write(
        tmp_path / "backend" / "config.example.yaml",
        "universe:\n  sectors: {MSFT: Technology}\n  names: {MSFT: Microsoft}\n"
        "risk:\n  drawdown_kill_pct: 8\n",
    )
    _write(
        tmp_path / "backend" / "config.yaml",
        "universe:\n  sector: {TSLA: Energy}\nrisk:\n  pdt_max_day_trades: 3\n",
    )
    with caplog.at_level(logging.WARNING, logger="app.config"):
        config = _config(tmp_path, monkeypatch)
    assert config.get("universe", "sector") is None
    assert config.get("risk", "pdt_max_day_trades") is None
    assert config.get("universe", "sectors", "MSFT") == "Technology"
    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "universe.sector.TSLA" in warned
    assert "risk.pdt_max_day_trades" in warned
    assert config.layer_counts()["unknown_keys"] == 2


def test_universe_symbols_list_is_kept_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """universe.symbols is a plain list: elements the example doesn't
    have are kept — lists were never key-filtered (item 052)."""
    _write(
        tmp_path / "backend" / "config.example.yaml",
        "universe:\n  symbols: [MSFT]\n  sectors: {MSFT: Technology}\n",
    )
    config = _config(tmp_path, monkeypatch)
    config.apply_saved({"universe": {"symbols": ["MSFT", "TSLA"]}}, 2)
    assert config.get("universe", "symbols") == ["MSFT", "TSLA"]


def test_local_only_model_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """models is an open map: a local model entry isn't an unknown key."""
    _write(tmp_path / "backend" / "config.example.yaml", "models: {}\n")
    _write(
        tmp_path / "backend" / "config.yaml",
        "models:\n  local-1: {provider: openai, input: 1, output: 2}\n",
    )
    with pytest.MonkeyPatch.context():
        config = _config(tmp_path, monkeypatch)
    assert config.get("models", "local-1", "provider") == "openai"
    assert config.layer_counts()["unknown_keys"] == 0


async def test_settings_save_refuses_unknown_model(
    session_factory: sessionmaker[Session],
) -> None:
    """The save path refuses a model missing from the merged list —
    including ask.model (docs/SPEC.md, Config layers)."""
    from app.broker.fake import FakeBroker
    from app.market.clock import FakeClock
    from app.settings_service import SettingsError, validate_settings

    from tests.helpers import NOW

    broker = FakeBroker(FakeClock(NOW))
    active = AppConfig().effective()
    with pytest.raises(SettingsError, match="gpt-9"):
        await validate_settings(
            {"decision": {"model": "gpt-9"}},
            active,
            broker=broker,
            held_symbols=set(),
        )
    with pytest.raises(SettingsError, match="Ask model"):
        await validate_settings(
            {"ask": {"model": "gpt-9"}},
            active,
            broker=broker,
            held_symbols=set(),
        )


def test_startup_warns_per_missing_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A model used by a task but absent from the merged list warns once
    at startup — it doesn't refuse (docs/SPEC.md, Config layers)."""
    from app.llm.models import check_models_at_startup

    config = AppConfig()
    config.apply_saved({"decision": {"model": "gpt-9"}, "ask": {"model": "claude-9"}}, 7)
    with session_factory() as session, caplog.at_level(
        logging.WARNING, logger="app.llm.models"
    ):
        check_models_at_startup(config, session)
    warned = {
        m for m in ("gpt-9", "claude-9") if any(m in r.getMessage() for r in caplog.records)
    }
    assert warned == {"gpt-9", "claude-9"}
