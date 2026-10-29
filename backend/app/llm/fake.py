"""The fake model client for tests and simulated mode (docs/SPEC.md →
Model providers).

One fake stands behind every provider: deterministic answers from the
input snapshot — a valid ``enter`` for symbols in ``enter_symbols``
(stop 2 × ATR below price, target at 2R), ``skip`` otherwise, ``hold``
for held positions; the decision.v2 shape picks the first offered
option. Review payloads (``{"kind", "facts"}``) answer with the plain
template built from the given facts. ``responses`` overrides per symbol;
``handler``/``error`` steer the review paths.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from app.llm.base import ModelResponse

# A test/simulated review handler answers from system prompt + payload.
FakeHandler = Callable[[str, str], str]


def default_review_reply(system: str, user: str) -> str:
    """Answer with the plain template built from the given facts — it
    passes the number check by construction."""
    from app.reviews.templates import plain_content

    payload = json.loads(user)
    return json.dumps(
        plain_content(str(payload.get("kind", "weekly")), payload.get("facts", {}))
    )


class FakeModelClient:
    """Deterministic model for every task; never touches the network."""

    provider = "fake"

    def __init__(
        self,
        responses: dict[str | tuple[str, str], str | dict[str, str]] | None = None,
        enter_symbols: set[str] | None = None,
        handler: FakeHandler | None = None,
        error: str | None = None,
        error_type: str | None = None,
        http_status: int | None = None,
        config: Any = None,
    ) -> None:
        self.responses = responses or {}
        self.enter_symbols = enter_symbols or {"MSFT"}
        self.handler = handler or default_review_reply
        self.error = error
        self.error_type = error_type
        self.http_status = http_status
        self.model = "fake-decision"
        # When a config is given the fake reports the model's real
        # provider (a simulated Claude shadow records "anthropic").
        self._config = config

    # ------------------------------------------------------------------
    # decision answers

    def _default(
        self, snapshot: dict[str, Any], *, v2: bool = False
    ) -> dict[str, Any]:
        symbol = snapshot["symbol"]
        if v2:
            return self._default_v2(snapshot)
        if snapshot["context"] == "held":
            return {
                "symbol": symbol,
                "action": "hold",
                "confidence": 0.55,
                "stop_price": None,
                "target_price": None,
                "thesis": f"{symbol}: no change; the entry thesis still holds.",
                "invalidation": None,
                "risks": [],
                "news_used": [],
            }
        price = snapshot.get("price")
        atr = snapshot.get("indicators_1h", {}).get("atr14")
        if (
            symbol in self.enter_symbols
            and isinstance(price, int | float)
            and isinstance(atr, int | float)
            and atr > 0
        ):
            stop = round(price - 2 * atr, 4)
            target = round(price + 2 * (price - stop), 4)
            return {
                "symbol": symbol,
                "action": "enter",
                "confidence": 0.62,
                "stop_price": stop,
                "target_price": target,
                "thesis": f"{symbol}: trend continuation above session VWAP.",
                "invalidation": f"Hourly close below {stop}.",
                "risks": ["Simulated decision"],
                "news_used": [],
            }
        return {
            "symbol": symbol,
            "action": "skip",
            "confidence": 0.4,
            "stop_price": None,
            "target_price": None,
            "thesis": f"{symbol}: signals conflict; waiting for a cleaner setup.",
            "invalidation": None,
            "risks": [],
            "news_used": [],
        }

    def _default_v2(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """The v2 answer shape: pick the first offered option, else skip."""
        symbol = snapshot["symbol"]
        base = {
            "symbol": symbol,
            "thesis": f"{symbol}: simulated decision.",
            "levels_used": [],
            "risks": ["Simulated decision"],
            "news_used": [],
        }
        if snapshot["context"] == "held":
            return {**base, "action": "hold", "option_id": None, "confidence": 0.55}
        options = snapshot.get("options") or []
        if symbol in self.enter_symbols and options:
            chosen = options[0]
            return {
                **base,
                "action": "enter",
                "option_id": chosen["id"],
                "confidence": 0.62,
                "levels_used": [
                    lv
                    for lv in (chosen.get("stop_level"), chosen.get("target_level"))
                    if lv
                ],
            }
        return {**base, "action": "skip", "option_id": None, "confidence": 0.4}

    async def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> ModelResponse:
        started = time.monotonic()
        use_model = model or self.model
        provider = "fake"
        if self._config is not None:
            from app.llm.models import model_provider

            provider = model_provider(self._config, use_model)
        snapshot = json.loads(user)
        if "facts" in snapshot and "symbol" not in snapshot:
            # A review payload {"kind", "facts"}.
            if self.error is not None:
                return ModelResponse(
                    raw=None,
                    model=use_model,
                    tokens_in=None,
                    tokens_out=None,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error=self.error,
                    error_type=self.error_type,
                    http_status=self.http_status,
                    provider=provider,
                )
            raw = self.handler(system, user)
            tokens_in = len(system + user) // 4
            return ModelResponse(
                raw=raw,
                model=use_model,
                tokens_in=tokens_in,
                tokens_out=len(raw) // 4,
                latency_ms=int((time.monotonic() - started) * 1000) or 1,
                parsed=json.loads(raw),
                provider=provider,
            )
        override = self.responses.get(
            (snapshot["symbol"], snapshot["context"]), self.responses.get(snapshot["symbol"])
        )
        if isinstance(override, dict) and "raw" not in override:
            # Tests exercise the error paths (a timeout, an HTTP 503, a
            # refusal) by adding error_type/http_status/request_id/refused.
            refused = bool(override.get("refused"))
            status = override.get("http_status")
            return ModelResponse(
                raw=None,
                model=use_model,
                tokens_in=None,
                tokens_out=None,
                latency_ms=int(override.get("latency_ms")
                               or (time.monotonic() - started) * 1000),
                error=None if refused else override.get("error", "fake error"),
                error_type=override.get("error_type"),
                http_status=int(status) if status is not None else None,
                error_code=override.get("error_code"),
                request_id=override.get("request_id"),
                refused=refused,
                provider=provider,
            )
        if isinstance(override, dict):
            override = str(override["raw"])
        raw = (
            override
            if isinstance(override, str)
            else json.dumps(self._default(snapshot, v2=_uses_v2(schema, snapshot)))
        )
        # Deterministic pseudo-usage so cost tracking is exercised end to end.
        tokens_in = len(system + user) // 4
        tokens_out = len(raw) // 4
        return ModelResponse(
            raw=raw,
            model=use_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=int((time.monotonic() - started) * 1000) or 1,
            parsed=json.loads(raw),
            provider=provider,
        )


def _uses_v2(schema: dict[str, Any] | None, snapshot: dict[str, Any]) -> bool:
    """True when the call uses the v2 contract (schema or option input)."""
    if schema is not None:
        props = (schema.get("schema") or {}).get("properties") or {}
        return "option_id" in props
    return "options" in snapshot or "stop_options" in snapshot
