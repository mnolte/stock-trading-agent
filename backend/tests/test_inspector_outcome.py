"""The call outcome rule (docs/SPEC.md, LLM call history — Outcome).

One pure function decides success / warning / error and the reason list.
It runs at write time for live, shadow and review rows and in the
backfill, so every call means the same thing. An error outranks every
warning; a call can carry several warning reasons.
"""

from app.inspector.outcome import CallFacts, classify, input_incomplete

SLOW_MS = 15_000


class TestErrorReasons:
    def test_timeout(self) -> None:
        assert classify(CallFacts(error_type="timeout")) == (
            "error",
            ["timeout"],
        )

    def test_api_error(self) -> None:
        assert classify(CallFacts(error_type="api_error")) == (
            "error",
            ["api_error"],
        )

    def test_invalid_output(self) -> None:
        assert classify(CallFacts(output_invalid=True)) == (
            "error",
            ["invalid_output"],
        )

    def test_refusal(self) -> None:
        assert classify(CallFacts(refused=True)) == ("error", ["refusal"])

    def test_error_beats_every_warning(self) -> None:
        outcome, reasons = classify(
            CallFacts(
                error_type="timeout",
                model_action="enter",
                final_action="blocked",
                input_incomplete=True,
                number_check_failed=True,
                latency_ms=SLOW_MS + 1,
                slow_ms=SLOW_MS,
            )
        )
        assert outcome == "error"
        assert reasons == ["timeout"]

    def test_error_plus_invalid_output_lists_both(self) -> None:
        # A 503 whose body also failed to parse is still one error row —
        # the reasons name everything that went wrong, in spec order.
        outcome, reasons = classify(
            CallFacts(error_type="api_error", output_invalid=True)
        )
        assert outcome == "error"
        assert reasons == ["api_error", "invalid_output"]


class TestWarningReasons:
    def test_changed_by_code(self) -> None:
        assert classify(
            CallFacts(model_action="enter", final_action="blocked")
        ) == ("warning", ["changed_by_code"])

    def test_gate_blocked_is_changed_by_code(self) -> None:
        assert classify(
            CallFacts(model_action="exit", final_action="blocked")
        ) == ("warning", ["changed_by_code"])

    def test_gate_reduced_size_is_not_a_warning(self) -> None:
        # The action survived: a smaller size is not a rename.
        assert classify(
            CallFacts(model_action="enter", final_action="enter")
        ) == ("success", [])

    def test_input_incomplete(self) -> None:
        assert classify(CallFacts(input_incomplete=True)) == (
            "warning",
            ["input_incomplete"],
        )

    def test_number_check_failed(self) -> None:
        assert classify(CallFacts(number_check_failed=True)) == (
            "warning",
            ["number_check_failed"],
        )

    def test_slow_above_threshold(self) -> None:
        assert classify(
            CallFacts(latency_ms=SLOW_MS + 1, slow_ms=SLOW_MS)
        ) == ("warning", ["slow"])

    def test_slow_at_threshold_is_not_slow(self) -> None:
        assert classify(
            CallFacts(latency_ms=SLOW_MS, slow_ms=SLOW_MS)
        ) == ("success", [])

    def test_slow_needs_a_latency(self) -> None:
        # Backfilled rows have no latency — they cannot be "slow".
        assert classify(CallFacts(latency_ms=None, slow_ms=SLOW_MS)) == (
            "success",
            [],
        )

    def test_several_warnings_in_spec_order(self) -> None:
        outcome, reasons = classify(
            CallFacts(
                model_action="enter",
                final_action="skip",
                input_incomplete=True,
                number_check_failed=True,
                latency_ms=SLOW_MS + 1,
                slow_ms=SLOW_MS,
            )
        )
        assert outcome == "warning"
        assert reasons == [
            "changed_by_code",
            "input_incomplete",
            "number_check_failed",
            "slow",
        ]


class TestSuccess:
    def test_clean_call(self) -> None:
        assert classify(CallFacts()) == ("success", [])

    def test_fast_call_with_matching_action(self) -> None:
        assert classify(
            CallFacts(
                model_action="hold",
                final_action="hold",
                latency_ms=800,
                slow_ms=SLOW_MS,
            )
        ) == ("success", [])


class TestInputIncomplete:
    """``input_incomplete`` reads the stored input snapshot/facts."""

    def test_unavailable_field(self) -> None:
        assert input_incomplete({"price": "unavailable"})

    def test_nested_and_listed(self) -> None:
        assert input_incomplete(
            {"indicators_1h": {"atr14": "unavailable"}}
        )
        assert input_incomplete(
            {"headlines": [{"title": "unavailable"}]}
        )

    def test_truncated_or_failed_headlines(self) -> None:
        assert input_incomplete({"news_status": "truncated"})
        assert input_incomplete({"news_status": "failed"})

    def test_complete_and_empty_headlines_are_fine(self) -> None:
        assert not input_incomplete({"news_status": "complete"})
        assert not input_incomplete({"news_status": "empty"})

    def test_full_snapshot_passes(self) -> None:
        assert not input_incomplete(
            {
                "symbol": "AAPL",
                "price": 252.1,
                "indicators_1h": {"atr14": 1.2},
                "news_status": "complete",
                "headlines": [],
            }
        )

    def test_rank_is_ignored(self) -> None:
        # Fixed mode never ranks: "unavailable" there is by design.
        assert not input_incomplete({"rank": "unavailable"})
        assert input_incomplete(
            {"rank": "unavailable", "price": "unavailable"}
        )

    def test_non_dict_is_not_incomplete(self) -> None:
        assert not input_incomplete(None)
        assert not input_incomplete("unavailable")
