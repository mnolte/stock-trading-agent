"""Entry selection (docs/SPEC.md, Entry selection — PR 33)."""

from __future__ import annotations

import random
from decimal import Decimal
from itertools import combinations

import pytest
from app.config import AppConfig
from app.engine.selection import (
    ScoredEntry,
    SelectionBudget,
    choose,
    score,
)
from app.risk.math import position_size


def _budget(**kw: object) -> SelectionBudget:
    base: dict[str, object] = {
        "free_slots": 4,
        "sector_cap": 2,
        "sector_counts": {},
        "open_risk_used": Decimal(0),
        "open_risk_cap": Decimal("50000"),
        "cash": Decimal("100000"),
        "orders_left": 6,
        "equity": Decimal("100000"),
    }
    base.update(kw)
    return SelectionBudget(**base)  # type: ignore[arg-type]


def _entry(
    symbol: str,
    *,
    rank: int | None,
    rr: str,
    confidence: str,
    entry: str = "100",
    stop: str = "99",
    sector: str | None = "Tech",
    equity: Decimal = Decimal("100000"),
    cash: Decimal = Decimal("100000"),
    open_risk: Decimal = Decimal(0),
    config: AppConfig | None = None,
) -> ScoredEntry:
    """A scored candidate whose dollars at risk come from the shared
    sizing function, exactly as the cycle computes them."""
    config = config or AppConfig()
    entry_d, stop_d = Decimal(entry), Decimal(stop)
    sizing = position_size(
        equity,
        entry_d,
        stop_d,
        cash=cash,
        current_open_risk=open_risk,
        config=config,
        risk_factor=Decimal(1),
    )
    assert sizing.shares >= 1, f"{symbol} does not size in the test fixture"
    dollars = sizing.shares * (entry_d - stop_d)
    be, p, er, ep = score(
        reward_to_risk=Decimal(rr),
        confidence=Decimal(confidence),
        dollars_at_risk=dollars,
        confidence_weight=Decimal("0.5"),
    )
    return ScoredEntry(
        symbol=symbol,
        rank=rank,
        sector=sector,
        entry=entry_d,
        stop=stop_d,
        reward_to_risk=Decimal(rr),
        breakeven=be,
        p_used=p,
        expected_r=er,
        dollars_at_risk=dollars,
        expected_profit=ep,
    )


def test_score_design_numbers() -> None:
    """The design's worked example: be, p, expected R, expected profit."""
    be, p, er, ep = score(
        reward_to_risk=Decimal("2.50"),
        confidence=Decimal("0.58"),
        dollars_at_risk=Decimal("51.53"),
        confidence_weight=Decimal("0.5"),
    )
    assert be == Decimal(1) / Decimal("3.5")
    assert p == be + Decimal("0.5") * (Decimal("0.58") - be)
    assert er.quantize(Decimal("0.01")) == Decimal("0.52")
    assert ep.quantize(Decimal("0.01")) == Decimal("26.54")


def test_choose_design_example_two_slots() -> None:
    """XOM + MSFT over rank order's AMZN + MSFT (design example)."""
    xom = _entry("XOM", rank=3, rr="2.5", confidence="0.58", sector="Energy")
    msft = _entry("MSFT", rank=2, rr="2.0", confidence="0.61", sector="Tech")
    amzn = _entry("AMZN", rank=1, rr="1.5", confidence="0.60", sector="Retail")
    res = choose(
        [xom, msft, amzn], _budget(free_slots=2), AppConfig()
    )
    assert [e.symbol for e in res.chosen] == ["XOM", "MSFT"]
    assert set(res.rank_keys) == {"AMZN", "MSFT"}
    risk = res.sized["XOM"][1]
    assert res.total == xom.expected_r * risk + msft.expected_r * risk
    assert res.rank_total == amzn.expected_r * risk + msft.expected_r * risk


def test_sector_cap_picks_best_fitting_set() -> None:
    """One sector place left: the set with the best Tech + the other."""
    a = _entry("AAA", rank=1, rr="2.0", confidence="0.90", sector="Tech")
    b = _entry("BBB", rank=2, rr="1.5", confidence="0.90", sector="Tech")
    c = _entry("CCC", rank=3, rr="1.5", confidence="0.80", sector="Health")
    res = choose(
        [a, b, c],
        _budget(free_slots=2, sector_counts={"Tech": 1}),
        AppConfig(),
    )
    assert {e.symbol for e in res.chosen} == {"AAA", "CCC"}


def test_open_risk_reduces_set_sizes() -> None:
    """A set that only fits with reduced sizes scores on those sizes."""
    config = AppConfig()
    equity = Decimal("100000")
    # room for only ~60 risk left: 2.5% of equity = 2500, used 2440.
    e = _entry("AAA", rank=1, rr="2.0", confidence="0.90", equity=equity)
    f = _entry("BBB", rank=2, rr="1.0", confidence="0.90", equity=equity)
    res = choose(
        [e, f],
        _budget(open_risk_used=Decimal("2440"), equity=equity,
                free_slots=2),
        config,
    )
    # Both entries fit individually (each sizes to the room); together
    # the second member gets whatever the first leaves — the set still
    # totals on the reduced size.
    sized = res.sized
    total_sized_risk = sum(v[1] for v in sized.values())
    assert total_sized_risk <= Decimal("2500") - Decimal("2440") + Decimal(1)


def test_expected_r_non_positive_never_chosen() -> None:
    weak = _entry("AAA", rank=1, rr="1.0", confidence="0.10")
    assert weak.expected_r < 0
    strong = _entry("BBB", rank=2, rr="2.0", confidence="0.90")
    res = choose([weak, strong], _budget(free_slots=2), AppConfig())
    assert [e.symbol for e in res.chosen] == ["BBB"]


def test_last_slot_floor_blocks_only_final_slot() -> None:
    a = _entry("AAA", rank=1, rr="2.0", confidence="0.90")
    b = _entry("BBB", rank=2, rr="1.5", confidence="0.62")
    # B's expected R sits below the floor.
    floor = b.expected_r + Decimal("0.01")
    two = choose(
        [a, b],
        _budget(free_slots=2, last_slot_min_ev_r=floor),
        AppConfig(),
    )
    assert [e.symbol for e in two.chosen] == ["AAA"]
    three = choose(
        [a, b],
        _budget(free_slots=3, last_slot_min_ev_r=floor),
        AppConfig(),
    )
    # With a spare slot left the same entry is allowed.
    assert {e.symbol for e in three.chosen} == {"AAA", "BBB"}


def test_tie_break_best_ranks_then_fewer() -> None:
    a = _entry("AAA", rank=1, rr="2.0", confidence="0.90")
    b = _entry("BBB", rank=2, rr="2.0", confidence="0.90")
    c = _entry("CCC", rank=3, rr="2.0", confidence="0.90", sector="Health")
    # Only one slot: equal expected profit everywhere, best rank wins.
    res = choose([a, b, c], _budget(free_slots=1), AppConfig())
    assert [e.symbol for e in res.chosen] == ["AAA"]


def test_orders_and_slots_cap_the_set() -> None:
    entries = [
        _entry(f"S{i}", rank=i, rr="2.0", confidence="0.90", sector=f"S{i}")
        for i in range(5)
    ]
    res = choose(
        entries, _budget(free_slots=4, orders_left=2), AppConfig()
    )
    assert len(res.chosen) == 2


def _brute_force_total(
    pool: list[ScoredEntry], budget: SelectionBudget, config: AppConfig
) -> Decimal:
    """Independent exhaustive check of every subset — the same rules as
    the module documents, written plainly for the property test."""
    best = Decimal(0)
    for size in range(1, len(pool) + 1):
        for subset in combinations(pool, size):
            if len(subset) > budget.free_slots or len(subset) > budget.orders_left:
                continue
            counts = dict(budget.sector_counts)
            ok = True
            for e in subset:
                if e.sector is None:
                    ok = False
                    break
                counts[e.sector] = counts.get(e.sector, 0) + 1
                if counts[e.sector] > budget.sector_cap:
                    ok = False
                    break
            if not ok:
                continue
            if (
                budget.last_slot_min_ev_r is not None
                and len(subset) == budget.free_slots
                and min(e.expected_r for e in subset) < budget.last_slot_min_ev_r
            ):
                continue
            cash = budget.cash
            open_risk_used = budget.open_risk_used
            total = Decimal(0)
            for e in sorted(subset, key=lambda e: -e.expected_profit):
                sizing = position_size(
                    budget.equity,
                    e.entry,
                    e.stop,
                    cash=cash,
                    current_open_risk=open_risk_used,
                    config=config,
                    risk_factor=Decimal(1),
                )
                if sizing.shares < 1:
                    total = Decimal(-1)
                    break
                r = e.entry - e.stop
                open_risk_used += sizing.shares * r
                cash -= sizing.shares * e.entry
                total += e.expected_r * sizing.shares * r
            if total > best:
                best = total
    return best


@pytest.mark.parametrize("seed", range(20))
def test_choose_matches_brute_force(seed: int) -> None:
    rng = random.Random(seed)
    config = AppConfig()
    pool = []
    for i in range(rng.randint(1, 7)):
        rr = Decimal(rng.choice(["1.0", "1.5", "2.0", "2.5", "3.0"]))
        conf = Decimal(rng.choice(["0.55", "0.6", "0.7", "0.8", "0.9"]))
        sector = rng.choice(["Tech", "Health", "Energy", "Retail"])
        entry_price = Decimal(rng.choice(["50", "80", "100", "200"]))
        stop_price = entry_price - Decimal(rng.choice(["1", "2", "5"]))
        pool.append(
            _entry(
                f"S{i}", rank=i + 1, rr=str(rr), confidence=str(conf),
                sector=sector,
                entry=str(entry_price),
                stop=str(stop_price),
            )
        )
    budget = _budget(
        free_slots=rng.randint(1, 4),
        sector_cap=2,
        sector_counts={},
        open_risk_used=Decimal(0),
        cash=Decimal(rng.choice(["50000", "100000"])),
        orders_left=rng.randint(1, 6),
        last_slot_min_ev_r=(
            Decimal("0.2") if rng.random() < 0.3 else None
        ),
    )
    res = choose(pool, budget, config)
    assert res.total == _brute_force_total(
        [e for e in pool if e.expected_r > 0][:10], budget, config
    )
    # Every chosen set obeys the limits.
    assert len(res.chosen) <= budget.free_slots
    assert len(res.chosen) <= budget.orders_left


def test_zero_pool_and_no_fitting_set() -> None:
    res = choose([], _budget(free_slots=0), AppConfig())
    assert res.chosen == ()
    assert res.total == Decimal(0)
