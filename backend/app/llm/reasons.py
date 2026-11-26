"""Every warning and error reason, explained (docs/SPEC.md → LLM call
history → Reasons explained; item 074).

One dict holds the fixed text for each reason code: what it means, the
likely cause and what to do, with the spec table's wording. ``changed_by_code``
adds one entry per change reason (the gate or check that changed the
action) and ``api_error`` one per HTTP status class; both reuse the parent
row's text where the spec gives a single sentence. The inspector serves
these texts with the call's own details filled in — the GUI never writes
its own explanation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReasonText:
    """The four parts of one explanation plus its link buttons.

    ``links`` are ``(label, kind)`` pairs; the API resolves each kind to
    the href that fits the call (the decision, Logs at that minute,
    Settings → API keys and data, …). ``detail`` is not stored here — it
    is the call's own specifics, built per call.
    """

    code: str
    title: str
    means: str
    cause: str
    fix: str
    links: tuple[tuple[str, str], ...] = ()


# Link kinds the API resolves per call.
LINK_DECISION = "decision"  # the linked decision, shadow or review
LINK_STRATEGY = "strategy"  # the shadow's strategy page
LINK_SETTINGS = "settings"  # Settings → Model and reviews
LINK_KEYS = "settings_keys"  # Settings → API keys and data
LINK_LOGS = "logs"  # Logs at the call's minute
LINK_INSIGHTS = "insights"  # Insights → What each filter removed
LINK_POSITIONS = "positions"  # the position the warning is about
LINK_REVIEWS = "reviews"


REASONS: dict[str, ReasonText] = {
    "changed_by_code": ReasonText(
        code="changed_by_code",
        title="Changed by code",
        means=(
            "The model's answer was valid, but code changed the action, "
            "usually enter → skip. The change reason names the check or gate"
        ),
        cause=(
            "A risk gate or code check did its job: reward to risk below "
            "the minimum, stop distance, spread, max positions, sector "
            "cap, total open risk, edge_too_small, flip_flop, "
            "price_stale, price_suspect, not_selected"
        ),
        fix=(
            "Usually nothing. Open the decision to see the gate's numbers. "
            "If one change reason keeps blocking good setups, check "
            "Insights → What each filter removed, and test another "
            "threshold in a shadow before changing it"
        ),
        links=(
            ("Open the decision", LINK_DECISION),
            ("What each filter removed", LINK_INSIGHTS),
        ),
    ),
    "input_incomplete": ReasonText(
        code="input_incomplete",
        title="Input incomplete",
        means=(
            "The model answered with part of its input missing. Decisions: "
            "a field was \"unavailable\", or the symbol's headlines were "
            "truncated or failed. Reviews: a fact was unavailable (for "
            "verdicts, usually the option touches)"
        ),
        cause=(
            "A data source failed or answered late at that cycle (news, "
            "quotes, FRED, the sector ETFs); the news request budget ran "
            "out (truncated); for verdicts, the 5-min bars for the "
            "5-session window were missing (item 058)"
        ),
        fix=(
            "Once: nothing. Repeating for one source: that source is "
            "failing; Needs you and Settings → API keys and data show it, "
            "and Logs has the error. Verdicts with missing touches settle "
            "by themselves after 2 sessions (outcomes.touches_give_up_sessions) "
            "and are marked \"unavailable\""
        ),
        links=(
            ("API keys and data", LINK_KEYS),
            ("Logs at that minute", LINK_LOGS),
        ),
    ),
    "number_check_failed": ReasonText(
        code="number_check_failed",
        title="Number check failed",
        means=(
            "A briefing, weekly review or Ask answer contained a number "
            "that isn't in its facts. The plain template (or \"no answer\") "
            "was shown instead"
        ),
        cause=(
            "The model rounded differently, computed a new number (a sum, "
            "a percentage) or invented one"
        ),
        fix=(
            "Once: nothing. Often: try another review model in Settings → "
            "Model and reviews. The facts and the text are both in this call"
        ),
        links=(
            ("Model and reviews", LINK_SETTINGS),
            ("Open in Reviews", LINK_REVIEWS),
        ),
    ),
    "slow": ReasonText(
        code="slow",
        title="Slow",
        means=(
            "The answer took longer than inspector.slow_call_seconds "
            "(15 s) but arrived"
        ),
        cause="The provider was busy, a long answer, or high reasoning effort",
        fix=(
            "Calls near 30 s risk a timeout. If they repeat, pick a faster "
            "model or a lower effort. Concurrency (item 078) doesn't make "
            "one call slower"
        ),
        links=(("Model and reviews", LINK_SETTINGS),),
    ),
    "unsupported_claim": ReasonText(
        code="unsupported_claim",
        title="Unsupported claim",
        means=(
            "The explanation names a level, claim or path the input "
            "doesn't support (decision.v2 and later). The decision stands"
        ),
        cause="The model described something it couldn't see in its input",
        fix=(
            "Read the flagged words in the raw response. Repeats under one "
            "prompt: test a change in a shadow"
        ),
        links=(("Open the decision", LINK_DECISION),),
    ),
    "unresolved_conflict": ReasonText(
        code="unresolved_conflict",
        title="Unresolved conflict",
        means=(
            "An enter listed a conflicting check without saying what "
            "outweighs it (decision.v4 and later). The decision stands"
        ),
        cause="The setup had a check against it that the model didn't weigh",
        fix=(
            "Open the decision's conflicts. Many of these: compare their "
            "results on Insights before trusting the prompt"
        ),
        links=(
            ("Open the decision", LINK_DECISION),
            ("Insights", LINK_INSIGHTS),
        ),
    ),
    "confidence_outlier": ReasonText(
        code="confidence_outlier",
        title="Confidence outlier",
        means=(
            "The stated confidence is more than 0.30 above its anchor: the "
            "option's break-even confidence, or the neutral probability "
            "for a held position (decision.v4 and later)"
        ),
        cause="Overconfidence; the model ignored the anchor it was given",
        fix=(
            "Nothing per call. Calibration on Insights shows whether "
            "confidence is overstated overall"
        ),
        links=(("Insights", LINK_INSIGHTS),),
    ),
    "best_option_missing": ReasonText(
        code="best_option_missing",
        title="Best option missing",
        means=(
            "A skip didn't name an offered option as best, or an enter "
            "named another option than the one it chose (decision.v4 and later)"
        ),
        cause="An answer that didn't follow the format for the best option",
        fix="Nothing per call. These decisions are left out of the skip calibration",
        links=(("Open the decision", LINK_DECISION),),
    ),
    "best_option_mismatch": ReasonText(
        code="best_option_mismatch",
        title="Best option mismatch",
        means=(
            "A skip didn't name an offered option as best, or an enter "
            "named another option than the one it chose (decision.v4 and later)"
        ),
        cause="An answer that didn't follow the format for the best option",
        fix="Nothing per call. These decisions are left out of the skip calibration",
        links=(("Open the decision", LINK_DECISION),),
    ),
    "hold_below_breakeven": ReasonText(
        code="hold_below_breakeven",
        title="Hold below break-even",
        means=(
            "A hold with confidence below the exit line, or held overnight "
            "below the overnight line with the stop within the usual gap "
            "(decision.v5)"
        ),
        cause="The model held a position its own numbers say to exit",
        fix=(
            "Look at the position: the Needs you card lists the overnight "
            "case the same evening"
        ),
        links=(
            ("Open the position", LINK_POSITIONS),
            ("Open the decision", LINK_DECISION),
        ),
    ),
    "timeout": ReasonText(
        code="timeout",
        title="Timeout",
        means=(
            "No answer within decision.call_timeout_seconds (30 s). Not "
            "retried; a decision becomes no_call (model_error) and nothing "
            "is traded for that symbol this cycle"
        ),
        cause="The provider was overloaded or the network failed",
        fix=(
            "Once: nothing. Several in one cycle: check the provider's "
            "status page. Every cycle: Logs shows whether the network or "
            "the provider fails"
        ),
        links=(("Logs at that minute", LINK_LOGS),),
    ),
    "api_error": ReasonText(
        code="api_error",
        title="API error",
        means="The provider answered with an HTTP error, or the connection failed. Not retried",
        cause=(
            "401 or 403: the key is wrong, expired or lacks access. "
            "404: the model name is unknown to the provider. "
            "429: rate limit or spending limit reached. "
            "500–599: the provider's own failure. "
            "No status: network or DNS"
        ),
        fix=(
            "401/403: replace the key in Settings → API keys and data. "
            "404: pick a listed model. "
            "429: raise the account's limits or lower "
            "concurrency.max_requests. "
            "5xx: wait; check the provider's status page. "
            "No status: check the server's network"
        ),
        links=(
            ("API keys and data", LINK_KEYS),
            ("Logs at that minute", LINK_LOGS),
        ),
    ),
    "invalid_output": ReasonText(
        code="invalid_output",
        title="Invalid output",
        means=(
            "The answer couldn't be used: not JSON, wrong schema, or it "
            "picked an option that wasn't offered. Counted as skip or hold"
        ),
        cause=(
            "The model ignored the format, or its answer was cut off at "
            "decision.max_output_tokens"
        ),
        fix=(
            "Once: nothing. Often for one model: that model doesn't follow "
            "the schema; pick another. Cut off: raise "
            "decision.max_output_tokens or lower the effort"
        ),
        links=(("Model and reviews", LINK_SETTINGS),),
    ),
    "refusal": ReasonText(
        code="refusal",
        title="Refusal",
        means="The model refused to answer. Counted as skip or hold",
        cause="A safety filter at the provider",
        fix="Read the refusal text. Repeats: report it with the call link",
        links=(),
    ),
}


@dataclass(frozen=True)
class ApiErrorClass:
    """The api_error explanation for one HTTP status class — the spec
    table's cause and fix fragments for that class."""

    key: str
    title_suffix: str
    cause: str
    fix: str


# api_error per status class (docs/SPEC.md, Reasons explained — api_error).
API_ERROR_CLASSES: dict[str, ApiErrorClass] = {
    "auth": ApiErrorClass(
        key="auth",
        title_suffix=" · key refused",
        cause="401 or 403: the key is wrong, expired or lacks access",
        fix="401/403: replace the key in Settings → API keys and data",
    ),
    "not_found": ApiErrorClass(
        key="not_found",
        title_suffix=" · unknown model",
        cause="404: the model name is unknown to the provider",
        fix="404: pick a listed model",
    ),
    "rate_limit": ApiErrorClass(
        key="rate_limit",
        title_suffix=" · rate limit",
        cause="429: rate limit or spending limit reached",
        fix=(
            "429: raise the account's limits or lower "
            "concurrency.max_requests"
        ),
    ),
    "server": ApiErrorClass(
        key="server",
        title_suffix=" · provider failure",
        cause="500–599: the provider's own failure",
        fix="5xx: wait; check the provider's status page",
    ),
    "network": ApiErrorClass(
        key="network",
        title_suffix=" · connection failed",
        cause="No status: network or DNS",
        fix="No status: check the server's network",
    ),
}


def api_error_class(http_status: int | None) -> ApiErrorClass:
    """The status class a stored api_error belongs to."""
    if http_status is None:
        return API_ERROR_CLASSES["network"]
    if http_status in (401, 403):
        return API_ERROR_CLASSES["auth"]
    if http_status == 404:
        return API_ERROR_CLASSES["not_found"]
    if http_status == 429:
        return API_ERROR_CLASSES["rate_limit"]
    return API_ERROR_CLASSES["server"]


@dataclass(frozen=True)
class ChangeReason:
    """What one change reason means — the check or gate that turned the
    model's action into the final one."""

    key: str
    title: str
    # A short cause phrase; None falls back to the parent row's cause.
    cause: str | None = None


# The canonical change reasons (docs/SPEC.md, Reasons explained —
# changed_by_code): the stored ``blocked_by``/``reason`` strings the code
# writes, plus the spec's names for the same checks. Dynamic check names
# ("Reward / risk ≥ 1.50") resolve through CHECK_REASON_PATTERNS.
CHANGE_REASONS: dict[str, ChangeReason] = {
    # Code checks (decide/output.py validation reasons).
    "rr_below_min": ChangeReason(
        "rr_below_min", "Reward to risk below the minimum",
        "reward to risk below the minimum",
    ),
    "stop_distance": ChangeReason(
        "stop_distance", "Stop distance outside the ATR band",
        "stop distance",
    ),
    "edge_too_small": ChangeReason(
        "edge_too_small", "Edge too small", "edge_too_small",
    ),
    "flip_flop": ChangeReason("flip_flop", "Flip-flop guard", "flip_flop"),
    "data_flag": ChangeReason("data_flag", "Data flag", "data_flag"),
    "sideways_rule": ChangeReason(
        "sideways_rule", "Sideways rule", "sideways_rule",
    ),
    "breakeven_too_early": ChangeReason(
        "breakeven_too_early", "Break-even guard", "breakeven_too_early",
    ),
    "trend_gate": ChangeReason(
        "trend_gate", "Trend gate", "the trend gate",
    ),
    # Entry-selection and quote gates (engine/executor, engine/cycle).
    "not_selected": ChangeReason(
        "not_selected", "Not selected", "not_selected",
    ),
    "price_stale": ChangeReason("price_stale", "Stale price", "price_stale"),
    "price_suspect": ChangeReason(
        "price_suspect", "Suspect price", "price_suspect",
    ),
    "quote_suspect": ChangeReason(
        "quote_suspect", "Suspect price", "price_suspect",
    ),
    "spread": ChangeReason("spread", "Spread too wide", "spread"),
    "missing_data": ChangeReason(
        "missing_data", "Missing data", "missing_data",
    ),
    "reference_missing": ChangeReason(
        "reference_missing", "Reference price missing", "reference_missing",
    ),
    "stop_market_distance": ChangeReason(
        "stop_market_distance", "Stop too close to the market",
        "stop_market_distance",
    ),
    "tighten_market_distance": ChangeReason(
        "tighten_market_distance", "Tighten too close to the market",
        "tighten_market_distance",
    ),
    "code_checks": ChangeReason(
        "code_checks", "Code checks", "code_checks",
    ),
    "fresh_quote": ChangeReason(
        "fresh_quote", "Fresh quote check", "fresh_quote",
    ),
    "account_unavailable": ChangeReason(
        "account_unavailable", "Account unavailable", "account_unavailable",
    ),
    "entry_cutoff": ChangeReason(
        "entry_cutoff", "Too close to the close", "entry_cutoff",
    ),
    # Risk gates (risk/gates.py evaluate_entry_gates).
    "kill_switch": ChangeReason("kill_switch", "Kill switch", "kill_switch"),
    "paused": ChangeReason("paused", "Entries paused", "paused"),
    "reconciliation": ChangeReason(
        "reconciliation", "Reconciliation", "reconciliation",
    ),
    "already_held": ChangeReason(
        "already_held", "Already held", "already_held",
    ),
    "entry_pending": ChangeReason(
        "entry_pending", "Entry pending", "entry_pending",
    ),
    "max_open_positions": ChangeReason(
        "max_open_positions", "Max open positions", "max positions",
    ),
    "sector_cap": ChangeReason("sector_cap", "Sector cap", "sector cap"),
    "sector_unknown": ChangeReason(
        "sector_unknown", "Sector unknown", "sector_unknown",
    ),
    "daily_loss_limit": ChangeReason(
        "daily_loss_limit", "Daily loss limit", "daily_loss_limit",
    ),
    "shock_brake": ChangeReason("shock_brake", "Shock brake", "shock_brake"),
    "earnings_blackout": ChangeReason(
        "earnings_blackout", "Earnings blackout", "earnings_blackout",
    ),
    "fomc_window": ChangeReason("fomc_window", "FOMC window", "fomc_window"),
    "reentry": ChangeReason("reentry", "Re-entry rule", "reentry"),
    "drawdown_kill": ChangeReason(
        "drawdown_kill", "Drawdown kill", "drawdown_kill",
    ),
    "working_entry_unpriced": ChangeReason(
        "working_entry_unpriced", "Working entry unpriced",
        "working_entry_unpriced",
    ),
    "risk_per_trade": ChangeReason(
        "risk_per_trade", "Risk per trade", "risk per trade",
    ),
    "max_position": ChangeReason(
        "max_position", "Max position size", "max position",
    ),
    "open_risk": ChangeReason(
        "open_risk", "Total open risk", "total open risk",
    ),
    "total_open_risk": ChangeReason(
        "total_open_risk", "Total open risk", "total open risk",
    ),
    "cash": ChangeReason("cash", "Cash", "cash"),
    "position_size": ChangeReason(
        "position_size", "Position size", "position_size",
    ),
    "order_sanity": ChangeReason(
        "order_sanity", "Order sanity", "order_sanity",
    ),
    "gate": ChangeReason("gate", "Risk gate", None),
}


# Dynamic check names become the stored change reason verbatim
# (``first_fail.name`` in decide/output.py carries the configured
# numbers): "Reward / risk ≥ 1.50", "Stop 1.0–3.0 × ATR (1h)",
# "Confidence ≥ …" (already normalised to edge_too_small upstream).
# First matching prefix wins.
CHECK_REASON_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Reward / risk", "rr_below_min"),
    ("Stop ", "stop_distance"),
    ("Confidence", "edge_too_small"),
    ("Option ", "option_check"),
    ("Room to first level", "room_to_level"),
)

CHANGE_REASONS["option_check"] = ChangeReason(
    "option_check", "Option check", None,
)
CHANGE_REASONS["room_to_level"] = ChangeReason(
    "room_to_level", "Room to first level", None,
)


def change_reason_key(stored: str | None) -> str | None:
    """The canonical CHANGE_REASONS key for a stored change reason.

    Exact match first, then the dynamic check-name patterns; anything
    else returns None and the caller falls back to the raw string.
    """
    if not stored:
        return None
    if stored in CHANGE_REASONS:
        return stored
    for prefix, key in CHECK_REASON_PATTERNS:
        if stored.startswith(prefix):
            return key
    return None


def change_reason_title(stored: str | None) -> str:
    """The display title for a stored change reason."""
    key = change_reason_key(stored)
    if key is not None:
        return CHANGE_REASONS[key].title
    if stored:
        return stored.replace("_", " ").capitalize()
    return "changed"
