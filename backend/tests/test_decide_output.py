"""Model output validation: every code check, pass and fail cases."""

import json
from decimal import Decimal

from app.config import AppConfig
from app.decide.output import ValidationResult, validate_model_output

CONFIG = AppConfig()


def _enter(**overrides: object) -> str:
    base: dict[str, object] = {
        "symbol": "MSFT",
        "action": "enter",
        "confidence": 0.62,
        "stop_price": 409.60,
        "target_price": 435.40,
        "thesis": "Pullback to the 1h EMA 20 held above session VWAP.",
        "invalidation": "Hourly close below 409.60.",
        "risks": ["Earnings in 9 sessions"],
        "news_used": ["n_1"],
    }
    base.update(overrides)
    return json.dumps(base)


def _validate(
    raw: str | None,
    *,
    context: str = "candidate",
    price: Decimal | None = Decimal("418.19"),
    atr: Decimal | None = Decimal("3.10"),
    stop: Decimal | None = None,
    call_error: str | None = None,
) -> ValidationResult:
    return validate_model_output(
        raw,
        symbol="MSFT",
        context=context,
        price=price,
        atr_1h=atr,
        current_stop=stop,
        call_error=call_error,
        config=CONFIG,
    )


def test_valid_enter_passes_all_checks() -> None:
    result = _validate(_enter())
    assert result.valid
    assert result.final_action == "enter"
    assert result.model_action == "enter"
    assert not result.changed_by_code
    assert all(c.passed for c in result.checks)


def test_enter_stop_too_close_fails() -> None:
    # 418.19 - 415.00 = 3.19 < 1.5 x ATR 3.10 (4.65).
    result = _validate(_enter(stop_price=415.00))
    assert result.valid
    assert result.final_action == "skip"
    assert result.changed_by_code
    check = next(c for c in result.checks if "ATR" in c.name)
    assert not check.passed


def test_enter_stop_too_far_fails() -> None:
    # 418.19 - 400.00 = 18.19 > 4 x ATR 3.10 (12.40).
    result = _validate(_enter(stop_price=400.00))
    assert result.final_action == "skip"


def test_enter_stop_above_price_fails() -> None:
    result = _validate(_enter(stop_price=420.00))
    assert result.final_action == "skip"


def test_enter_low_reward_to_risk_fails() -> None:
    # (420.00 - 418.19) / 8.59 = 0.21 < 1.5.
    result = _validate(_enter(target_price=420.00))
    assert result.final_action == "skip"
    check = next(c for c in result.checks if "Reward" in c.name)
    assert not check.passed


def test_enter_low_confidence_fails() -> None:
    result = _validate(_enter(confidence=0.40))
    assert result.final_action == "skip"
    check = next(c for c in result.checks if "Confidence" in c.name)
    assert not check.passed


def test_enter_missing_stop_or_target_fails() -> None:
    assert _validate(_enter(stop_price=None)).final_action == "skip"
    assert _validate(_enter(target_price=None)).final_action == "skip"


def test_enter_unavailable_price_fails() -> None:
    result = _validate(_enter(), price=None)
    assert result.final_action == "skip"


def test_skip_passes_through() -> None:
    result = _validate(_enter(action="skip", stop_price=None, target_price=None))
    assert result.valid
    assert result.final_action == "skip"


def test_invalid_json_counts_as_skip_for_candidate() -> None:
    result = _validate("this is not json")
    assert not result.valid
    assert result.final_action == "skip"
    assert result.error is not None


def test_invalid_json_counts_as_hold_for_position() -> None:
    result = _validate("this is not json", context="held")
    assert not result.valid
    assert result.final_action == "hold"


def test_schema_violation_is_invalid() -> None:
    result = _validate(_enter(confidence="high"))
    assert not result.valid
    assert result.final_action == "skip"


def test_unknown_field_is_invalid() -> None:
    result = _validate(_enter(extra_field=1))
    assert not result.valid


def test_action_not_allowed_for_context() -> None:
    result = _validate(_enter(action="exit"))
    assert not result.valid
    assert result.final_action == "skip"
    # A candidate cannot pick held actions either.
    result = _validate(_enter(action="hold"))
    assert result.final_action == "skip"


def test_symbol_mismatch_is_invalid() -> None:
    result = _validate(_enter(symbol="AAPL"))
    assert not result.valid
    assert result.final_action == "skip"


def test_tighten_stop_that_raises_passes() -> None:
    raw = _enter(action="tighten_stop", stop_price=412.00, target_price=None)
    result = _validate(raw, context="held", stop=Decimal("409.60"))
    assert result.valid
    assert result.final_action == "tighten_stop"


def test_tighten_stop_that_lowers_becomes_hold() -> None:
    raw = _enter(action="tighten_stop", stop_price=405.00, target_price=None)
    result = _validate(raw, context="held", stop=Decimal("409.60"))
    assert result.valid
    assert result.final_action == "hold"
    assert result.changed_by_code


def test_tighten_stop_without_current_stop_becomes_hold() -> None:
    raw = _enter(action="tighten_stop", stop_price=412.00, target_price=None)
    result = _validate(raw, context="held", stop=None)
    assert result.final_action == "hold"


def test_held_exit_and_hold_pass_through() -> None:
    for action in ("exit", "hold"):
        raw = _enter(action=action, stop_price=None, target_price=None)
        result = _validate(raw, context="held")
        assert result.valid
        assert result.final_action == action


def test_call_failure_is_invalid() -> None:
    result = _validate(None, call_error="timeout after 60s")
    assert not result.valid
    assert result.final_action == "skip"
    assert "model call failed" in (result.error or "")


def test_empty_response_is_invalid() -> None:
    result = _validate("   ")
    assert not result.valid
