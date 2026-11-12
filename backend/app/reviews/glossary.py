"""The review glossary (docs/SPEC.md, Reviews — Stories, item 079).

One plain sentence per term, from one fixed list in code, so the same
term is explained the same way every day. ``glossary_for`` returns only
the terms whose facts are present; a definition that needs a threshold
reads it from settings and the threshold also goes into the facts
(``limits``), so the model can never write a number code didn't compute.
"""

from __future__ import annotations

from typing import Any

from app.config import AppConfig


def _pct(value: float) -> str:
    return f"{value:g}%"


def glossary_for(facts: dict[str, Any], config: AppConfig) -> dict[str, str]:
    """term → one-sentence definition, for the terms this fact set uses."""
    out: dict[str, str] = {}
    kind = facts.get("kind")

    def _weekly_trades() -> list[dict[str, Any]]:
        trades = facts.get("trades")
        return [t for t in trades if isinstance(t, dict)] if isinstance(trades, list) else []

    if kind == "briefing":
        positions = facts.get("positions")
        if isinstance(positions, list) and any(
            isinstance(p, dict) and p.get("stop") is not None for p in positions
        ):
            out["stop"] = (
                "The price at which Alpaca sells a position automatically, "
                "to cap the loss."
            )
        regime = facts.get("regime")
        if isinstance(regime, str) and regime and regime != "unavailable":
            out["regime"] = (
                "The state of the whole market, from SPY: its trend "
                "(up, sideways, down) and how much it moves (volatility)."
            )
        vix = facts.get("vix")
        if isinstance(vix, dict) and vix.get("close") is not None:
            out["VIX"] = (
                "A measure of how much the market expects prices to swing "
                "over the next month. Higher means more nervous."
            )
            if vix.get("percentile_1y") is not None:
                out["percentile"] = (
                    "Where the VIX close sits within the past year: a "
                    "higher percentile means it was lower on fewer days."
                )
        if facts.get("next_release") is not None:
            out["release"] = (
                "A government statistic published at a fixed time, such as "
                "inflation (CPI). Prices often jump when it lands."
            )
        earnings = facts.get("earnings_today") or facts.get("earnings_tomorrow")
        if earnings:
            days = int(
                config.get("risk", "earnings_blackout_days", default=3)
            )
            out["earnings blackout"] = (
                f"No new entries within {days} trading days of a company's "
                "earnings report."
            )
        return out

    if kind == "weekly":
        trades = _weekly_trades()
        exits = {t.get("exit") for t in trades}
        if facts.get("avg_r") is not None or any(t.get("r") is not None for t in trades):
            out["R"] = (
                "What a trade risks: entry minus stop, times the shares. "
                "A result in R says how many times the risked amount was "
                "earned or lost."
            )
        if "target" in exits:
            out["target"] = (
                "The price at which Alpaca sells automatically to take the "
                "planned profit."
            )
        if "stop" in exits:
            out["stop"] = (
                "The price at which Alpaca sells a position automatically, "
                "to cap the loss."
            )
        if facts.get("max_drawdown_pct") is not None:
            kill = float(config.get("risk", "drawdown_kill_pct", default=8))
            out["drawdown"] = (
                "How far the account fell from its highest point. The kill "
                f"switch turns on at −{_pct(kill)}."
            )
        if facts.get("return_usd") is not None or facts.get("return_pct") is not None:
            out["adjusted P&L"] = (
                "The result after costs: the assumed slippage and "
                "dividends are taken out."
            )
        if (facts.get("won") or 0) + (facts.get("lost") or 0) > 0:
            out["win rate"] = "The share of closed trades that made money."
        if facts.get("avg_r") is not None:
            out["expectancy"] = (
                "What a trade earns on average, measured in R."
            )
        sample = facts.get("sample")
        if facts.get("trades_closed") or isinstance(sample, dict):
            out["sample"] = (
                "How much evidence the result rests on; with few trades "
                "one can still swing it either way."
            )
        regimes = facts.get("by_regime")
        if isinstance(regimes, list) and regimes:
            out["regime"] = (
                "The state of the whole market, from SPY: its trend "
                "(up, sideways, down) and how much it moves (volatility)."
            )
            if any(
                "volatility" in str(r.get("name", "")) for r in regimes if isinstance(r, dict)
            ):
                out["volatility"] = (
                    "How much prices swing. In high volatility a stop is "
                    "reached more easily by noise."
                )
        verdicts = facts.get("verdicts")
        if isinstance(verdicts, dict) and (verdicts.get("total") or 0) > 0:
            out["verdicts"] = (
                "Your judgement of each model decision, once its "
                "5-session result is in: good call, bad call or unclear."
            )
    return out
