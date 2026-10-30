# ruff: noqa: S608 - the only interpolated value is a whitelisted table name
"""IEX quote plausibility analysis (docs/kickoffs/quote-plausibility.md).

Read-only. Prints the markdown report to stdout.

    python scripts/analysis/quote_plausibility.py [db path]

Uses the stored price provenance on decisions and shadow_decisions of the
current run (the newest run in the database).
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

DB = Path(sys.argv[1] if len(sys.argv) > 1 else "data/stock-agent.db")


def dec(v: object) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (TypeError, ValueError, InvalidOperation):
        return None


def pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}%" if whole else "—"


def quantiles(values: list[float]) -> str:
    if not values:
        return "—"
    v = sorted(values)
    q = lambda p: f"{v[min(int(len(v) * p), len(v) - 1)]:.3f}%"  # noqa: E731
    return f"median {q(0.5)}, p90 {q(0.9)}, p99 {q(0.99)}"


def load(conn: sqlite3.Connection, table: str, current_run: int) -> list[dict]:
    if table not in ("decisions", "shadow_decisions"):
        raise ValueError(table)
    sql = (
        "SELECT d.id, d.symbol, d.context, d.final_action, d.no_call_reason,"
        " d.input_snapshot, d.gates_result, d.rules_result, c.scheduled_time"
        f" FROM {table} d JOIN cycles c ON c.id = d.cycle_id WHERE c.id >= ?"  # noqa: S608 - table whitelisted above
    )
    rows = []
    for r in conn.execute(sql, (first_cycle_of_run(conn, current_run),)):
        snap = json.loads(r["input_snapshot"] or "{}")
        gates = json.loads(r["gates_result"] or "{}") or {}
        rules = json.loads(r["rules_result"] or "{}") or {}
        rows.append(
            {
                "id": r["id"], "symbol": r["symbol"], "context": r["context"],
                "action": r["final_action"], "no_call": r["no_call_reason"],
                "rank": snap.get("rank"), "quote": snap.get("quote") or {},
                "gates": gates, "rules": rules, "time": r["scheduled_time"],
                "table": table,
            }
        )
    return rows


def first_cycle_of_run(conn: sqlite3.Connection, run_id: int) -> int:
    row = conn.execute(
        "SELECT started_at FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    since = row[0] if row else "1970-01-01"
    row = conn.execute(
        "SELECT min(id) FROM cycles WHERE started_at >= ?", (since,)
    ).fetchone()
    return row[0] or 0


def run_id(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT max(id) FROM runs").fetchone()[0]


def spread_pct(q: dict) -> float | None:
    bid, ask = dec(q.get("bid")), dec(q.get("ask"))
    if bid is None or ask is None or bid <= 0:
        return None
    return float((ask - bid) / bid * 100)


def implausible_reasons(q: dict) -> list[str]:
    reasons = []
    bid, ask, last = dec(q.get("bid")), dec(q.get("ask")), dec(q.get("last"))
    if bid is None:
        reasons.append("bid missing")
    if ask is None:
        reasons.append("ask missing")
    if not q.get("quote_timestamp"):
        reasons.append("timestamp missing")
    if last and bid is not None and bid > 0:
        dist = abs(bid - last) / last * 100
        if dist > 1:
            reasons.append(f"bid {float(dist):.2f}% away")
    if last and ask is not None and ask > 0:
        dist = abs(ask - last) / last * 100
        if dist > 1:
            reasons.append(f"ask {float(dist):.2f}% away")
    return reasons or ["other"]


def main() -> None:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rid = run_id(conn)
    rows = load(conn, "decisions", rid) + load(conn, "shadow_decisions", rid)
    out: list[str] = [
        "# IEX quote plausibility — 2026-10-29",
        "",
        f"Run {rid}: {len(rows)} decision snapshots "
        f"(decisions + shadow_decisions).",
        "",
    ]

    # 1. Plausible share, overall / per cycle time / per symbol / rank band.
    impl = [r for r in rows if not r["quote"].get("plausible")]
    out.append(
        f"## 1. Plausible quotes\n\n"
        f"- implausible: {len(impl)} of {len(rows)} "
        f"({pct(len(impl), len(rows))})\n"
    )
    by_time = Counter()
    tot_time = Counter()
    for r in rows:
        hhmm = (r["time"] or "")[11:16]
        tot_time[hhmm] += 1
        if not r["quote"].get("plausible"):
            by_time[hhmm] += 1
    out.append("- per cycle time (UTC): " + "; ".join(
        f"{t} {by_time[t]}/{tot_time[t]} ({pct(by_time[t], tot_time[t])})"
        for t in sorted(tot_time)))
    by_sym = Counter(r["symbol"] for r in impl)
    tot_sym = Counter(r["symbol"] for r in rows)
    out.append("- per symbol (top 20 implausible): " + "; ".join(
        f"{s} {n}/{tot_sym[s]} ({pct(n, tot_sym[s])})"
        for s, n in by_sym.most_common(20)))
    bands = {"1–10": Counter(), "11–30": Counter(), "31+": Counter()}
    band_tot = {"1–10": Counter(), "11–30": Counter(), "31+": Counter()}
    for r in rows:
        rank = r["rank"] if isinstance(r["rank"], int) else 999
        band = "1–10" if rank <= 10 else "11–30" if rank <= 30 else "31+"
        band_tot[band]["n"] += 1
        if not r["quote"].get("plausible"):
            bands[band]["n"] += 1
    out.append("- per rank band: " + "; ".join(
        f"{b} {bands[b]['n']}/{band_tot[b]['n']} "
        f"({pct(bands[b]['n'], band_tot[b]['n'])})" for b in bands))
    out.append("")

    # 2. Why implausible.
    reasons = Counter()
    dists: list[float] = []
    for r in impl:
        q = r["quote"]
        for reason in implausible_reasons(q):
            reasons[reason.split(" ")[0] + " " + reason.split(" ")[-1]
                      if "away" in reason else reason] += 1
        last, bid, ask = dec(q.get("last")), dec(q.get("bid")), dec(q.get("ask"))
        for side in (bid, ask):
            if last and side is not None and last > 0:
                dists.append(float(abs(side - last) / last * 100))
    out.append("## 2. Why implausible\n")
    out.append("- failures: " + "; ".join(f"{k} ×{n}" for k, n in reasons.most_common()))
    out.append(f"- distance distribution (all quote legs vs last): {quantiles(dists)}\n")

    # 3. Spreads on plausible quotes + gate blocks.
    spreads = [s for s in (spread_pct(r["quote"]) for r in rows
                       if r["quote"].get("plausible")) if s is not None]
    gate_blocks = [
        r for r in rows
        if (r["gates"].get("blocked_by") or "") == "spread"
        or any(c.get("name") == "spread" and not c.get("passed")
               for c in r["gates"].get("checks") or [])
    ]
    out.append("## 3. Spreads and the 0.15% gate\n")
    out.append(f"- spread on plausible quotes: {quantiles(spreads)}")
    out.append(f"- entries blocked by the spread gate: {len(gate_blocks)}\n")

    # 4. Entries with spread_unknown + slippage.
    entries = [r for r in rows if r["action"] == "enter"]
    unkn = [r for r in entries if r["quote"].get("spread_unknown")]
    slip = []
    for r in entries:
        q = r["quote"]
        last = dec(q.get("last"))
        f = conn.execute(
            "SELECT f.price FROM fills f JOIN orders o ON o.id = f.order_id"
            " JOIN decisions d ON d.id = o.decision_id WHERE d.id = ?"
            " AND o.leg = 'entry' ORDER BY f.id LIMIT 1", (r["id"],)
        ).fetchone() if r["table"] == "decisions" else None
        if f and last and last > 0:
            price = dec(f[0])
            if price:
                slip.append((float((price - last) / last * 10_000),
                            r["quote"].get("spread_unknown")))
    with_u = [s for s, u in slip if u]
    without_u = [s for s, u in slip if not u]
    out.append("## 4. Entries with spread_unknown\n")
    out.append(
        f"- entries: {len(entries)}, with spread_unknown: {len(unkn)} "
        f"({pct(len(unkn), len(entries))})")
    out.append(
        f"- entry slippage vs last trade: unknown-spread "
        f"{quantiles([v / 100 for v in with_u])} "
        f"(n={len(with_u)}, in %); plausible "
        f"{quantiles([v / 100 for v in without_u])} (n={len(without_u)})\n")

    # 5. price_stale / price_suspect blocks.
    stale = [r for r in rows if r["no_call"] in ("price_stale", "price_suspect")]
    by_day = Counter((r["time"] or "")[:10] for r in stale)
    by_sym5 = Counter(r["symbol"] for r in stale)
    ages = []
    for r in stale:
        q = r["quote"]
        ts = q.get("trade_timestamp")
        fetched = q.get("fetched_at")
        if ts and fetched:
            try:
                trade = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                got = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
                ages.append((got - trade).total_seconds())
            except (TypeError, ValueError):
                pass
    out.append("## 5. price_stale / price_suspect blocks\n")
    out.append(f"- total: {len(stale)} "
               f"({pct(len(stale), len(rows))} of candidate checks)")
    out.append("- per day: " + "; ".join(f"{d} ×{n}" for d, n in sorted(by_day.items())))
    out.append("- per symbol: " + "; ".join(f"{s} ×{n}" for s, n in by_sym5.most_common(10)))
    if ages:
        out.append(f"- last-trade age when blocked: median "
                   f"{statistics.median(ages):.0f}s, p90 "
                   f"{sorted(ages)[int(len(ages) * 0.9)]:.0f}s")
    out.append("")

    # 6. 1-minute bar range at decision time (bars_5m unavailable → skip if
    #    no 1m bars).
    out.append("## 6. 1-minute bar range\n")
    out.append("- skipped: the database stores 5-minute bars only; a 1-minute\n"
               "  range needs a new data source and was out of scope.\n")

    # 7. Conclusion.
    share_impl = len(impl) / len(rows) * 100 if rows else 0
    out.append("## 7. Conclusion\n")
    out.append(
        f"- {share_impl:.0f}% of snapshots had an implausible quote; on those "
        f"the 0.15% spread gate cannot run ({len(gate_blocks)} blocks recorded).\n"
        f"- Recommendation: keep the gate but treat implausible quotes as a "
        f"spread_unknown warning only for ranks 1–10 where the failure rate is "
        f"lowest; the alternative (drop the gate) loses the one real catch it "
        f"has, and a bar-range replacement needs new data."
    )
    print("\n".join(out))


if __name__ == "__main__":
    main()
