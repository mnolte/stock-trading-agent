"""Entry selection by expected profit (docs/SPEC.md, Entry selection).

Pure functions, no I/O: the scoring math (break-even, p used, expected R,
expected profit), the exact best-set search over at most 10 scored
candidates, and the rank-order total the old behaviour would have taken
under the same limits. Sizing always goes through the one shared
``position_size`` function — the same caps the gates apply, computed
against the budget left after the earlier entries of a set.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from typing import TYPE_CHECKING

from app.risk.math import position_size

if TYPE_CHECKING:
    from app.config import AppConfig


@dataclass(frozen=True)
class ScoredEntry:
    """One candidate that passed validation and the dry-run gates."""

    symbol: str
    rank: int | None
    sector: str | None
    entry: Decimal  # entry limit the sizing used
    stop: Decimal
    reward_to_risk: Decimal
    breakeven: Decimal  # 1 / (1 + R/R)
    p_used: Decimal  # be + confidence_weight x (confidence - be)
    expected_r: Decimal  # p x (1 + R/R) - 1
    dollars_at_risk: Decimal  # dry-run shares x (entry - stop)
    expected_profit: Decimal  # expected R x dollars at risk
    decision_id: int | None = None
    pre_score: Decimal | None = None  # dollars at risk of option A's size
    call_position: int | None = None  # 1-based position in the call order

    @property
    def key(self) -> str:
        return str(self.decision_id) if self.decision_id is not None else self.symbol


@dataclass(frozen=True)
class SelectionBudget:
    """The limits a chosen set must fit, from the cycle's starting state."""

    free_slots: int  # max positions - open positions - working entries
    sector_cap: int  # max positions per sector
    sector_counts: dict[str, int]  # open + working per sector already
    open_risk_used: Decimal  # open risk incl. working entries
    open_risk_cap: Decimal  # equity x max_open_risk_pct / 100
    cash: Decimal  # cash left after working entries
    orders_left: int  # max_orders_per_cycle - orders submitted this cycle
    equity: Decimal
    last_slot_min_ev_r: Decimal | None = None


@dataclass(frozen=True)
class SelectionResult:
    """The winning set plus the comparison against rank order."""

    chosen: tuple[ScoredEntry, ...]  # descending expected-profit order
    total: Decimal  # winning set's total expected profit
    rank_total: Decimal  # what rank order would have totalled
    rank_keys: tuple[str, ...]  # the set rank order would have taken
    sized: dict[str, tuple[int, Decimal]]  # key -> (shares, dollars at risk)


def score(
    *,
    reward_to_risk: Decimal,
    confidence: Decimal,
    dollars_at_risk: Decimal,
    confidence_weight: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """(be, p, expected R, expected profit) for one scored candidate."""
    breakeven = Decimal(1) / (1 + reward_to_risk)
    p_used = breakeven + confidence_weight * (confidence - breakeven)
    expected_r = p_used * (1 + reward_to_risk) - 1
    return breakeven, p_used, expected_r, expected_r * dollars_at_risk


def _rank_key(entry: ScoredEntry) -> tuple[int, str]:
    return (entry.rank if entry.rank is not None else 10**9, entry.symbol)


def _set_sectors_ok(
    subset: tuple[ScoredEntry, ...], budget: SelectionBudget
) -> bool:
    counts: dict[str, int] = dict(budget.sector_counts)
    for entry in subset:
        if entry.sector is None:
            return False  # sector_unknown blocks in the gates; same here
        counts[entry.sector] = counts.get(entry.sector, 0) + 1
        if counts[entry.sector] > budget.sector_cap:
            return False
    return True


def _eval_set(
    subset: tuple[ScoredEntry, ...],
    budget: SelectionBudget,
    config: AppConfig,
    risk_factor: Decimal,
) -> tuple[Decimal, dict[str, tuple[int, Decimal]]] | None:
    """Total expected profit of one set, or None when it does not fit.

    Sizes are worked out in descending expected-profit order, each
    against the budget left after the earlier ones — the same rule the
    gates apply to entries submitted in one cycle.
    """
    if len(subset) > budget.free_slots or len(subset) > budget.orders_left:
        return None
    if not _set_sectors_ok(subset, budget):
        return None
    if (
        budget.last_slot_min_ev_r is not None
        and len(subset) == budget.free_slots
        and min(e.expected_r for e in subset) < budget.last_slot_min_ev_r
    ):
        return None
    cash = budget.cash
    open_risk = budget.open_risk_used
    sized: dict[str, tuple[int, Decimal]] = {}
    total = Decimal(0)
    for entry in sorted(subset, key=lambda e: -e.expected_profit):
        sizing = position_size(
            budget.equity,
            entry.entry,
            entry.stop,
            cash=cash,
            current_open_risk=open_risk,
            config=config,
            risk_factor=risk_factor,
        )
        if sizing.shares < 1:
            return None
        risk = entry.entry - entry.stop
        open_risk += sizing.shares * risk
        cash -= sizing.shares * entry.entry
        sized[entry.key] = (sizing.shares, sizing.shares * risk)
        total += entry.expected_r * sizing.shares * risk
    return total, sized


def _set_rank_key(subset: tuple[ScoredEntry, ...]) -> tuple[tuple[int, ...], int]:
    """Tie-break: the set with the best ranks, then fewer orders."""
    return (
        tuple(sorted(_rank_key(e)[0] for e in subset)),
        len(subset),
    )


def choose(
    scored: list[ScoredEntry],
    budget: SelectionBudget,
    config: AppConfig,
    *,
    risk_factor: Decimal = Decimal(1),
) -> SelectionResult:
    """The set of scored entries with the highest total expected profit.

    At most 10 candidates, so every set is tried. The result also carries
    the total the rank order would have chosen under the same rules.
    """
    best: tuple[Decimal, dict[str, tuple[int, Decimal]]] | None = None
    best_subset: tuple[ScoredEntry, ...] = ()
    # Only positive expected profit counts; expected R <= 0 never enters
    # the search at all (the caller already filtered, this is a guard).
    pool = [e for e in scored if e.expected_r > 0][:10]
    for size in range(1, len(pool) + 1):
        for subset in combinations(pool, size):
            result = _eval_set(subset, budget, config, risk_factor)
            if result is None:
                continue
            if best is None or result[0] > best[0] or (
                result[0] == best[0]
                and _set_rank_key(subset) < _set_rank_key(best_subset)
            ):
                best = result
                best_subset = subset

    # The rank-order comparison: the same entries, walked best rank
    # first, each taken when it still fits — today's behaviour.
    rank_cash = budget.cash
    rank_risk = budget.open_risk_used
    rank_sectors: dict[str, int] = dict(budget.sector_counts)
    rank_total = Decimal(0)
    rank_keys: list[str] = []
    slots = 0
    for entry in sorted(pool, key=_rank_key):
        if slots >= budget.free_slots or slots >= budget.orders_left:
            break
        if entry.sector is None:
            continue
        if rank_sectors.get(entry.sector, 0) >= budget.sector_cap:
            continue
        if (
            budget.last_slot_min_ev_r is not None
            and slots == budget.free_slots - 1
            and entry.expected_r < budget.last_slot_min_ev_r
        ):
            continue
        sizing = position_size(
            budget.equity,
            entry.entry,
            entry.stop,
            cash=rank_cash,
            current_open_risk=rank_risk,
            config=config,
            risk_factor=risk_factor,
        )
        if sizing.shares < 1:
            continue
        risk = entry.entry - entry.stop
        rank_risk += sizing.shares * risk
        rank_cash -= sizing.shares * entry.entry
        rank_sectors[entry.sector] = rank_sectors.get(entry.sector, 0) + 1
        slots += 1
        rank_keys.append(entry.key)
        rank_total += entry.expected_r * sizing.shares * risk

    chosen = (
        tuple(sorted(best_subset, key=lambda e: -e.expected_profit))
        if best is not None
        else ()
    )
    return SelectionResult(
        chosen=chosen,
        total=best[0] if best is not None else Decimal(0),
        rank_total=rank_total,
        rank_keys=tuple(rank_keys),
        sized=best[1] if best is not None else {},
    )
