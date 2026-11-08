/** Pure helpers for the decision drawer and the dashboard's latest-decision card. */

import type {
  CycleSelection,
  DecisionDetail,
  DecisionEarnings,
  SelectionRecord,
} from '../types/api';
import { actionLabel } from '../components/decisionFormat';

export type GateState = 'pass' | 'fail' | 'reduced' | 'warning';

export interface GateRow {
  name: string;
  detail: string;
  state: GateState;
}

interface GatesResult {
  risk_factor?: string;
  sizing?: { shares?: number; limited_by?: string[] } | null;
  checks?: { name: string; passed: boolean; detail: string }[];
}

// Labels that differ from the humanised gate name
// (design/screens/Decision.dc.html: "Not traded today — MSFT · pass").
const GATE_LABELS: Record<string, string> = {
  reentry: 'Not traded today',
};

/** decision.v5 rows ahead of the stored checks: the trend gate that ran
 * in code before the model, and the session time left against the
 * chosen option's target distance (docs/SPEC.md, Decision v5 — GUI). */
function v5FirstRows(decision: DecisionDetail): GateRow[] {
  if (decision.prompt_version !== 'decision.v5') return [];
  const rows: GateRow[] = [];
  const checks = (decision.input_snapshot?.checks ?? {}) as {
    trend_1h?: string;
    trend_daily?: string;
    regime_trend?: string;
  };
  const trendPass = checks.trend_1h === 'up' && checks.regime_trend === 'up';
  rows.push({
    name: 'Trend gate (code, before the model)',
    detail:
      decision.model_output === null
        ? `the model was not called · ${decision.no_call_reason ?? 'checks'}`
        : `trend 1h ${checks.trend_1h ?? '—'} · regime ${checks.regime_trend ?? '—'} · ${trendPass ? 'pass' : 'fail'}`,
    state: decision.model_output === null ? 'fail' : trendPass ? 'pass' : 'warning',
  });
  const session = decision.input_snapshot?.session as
    | { minutes_left?: number | null; last_cycle?: boolean }
    | undefined;
  const options = (decision.options?.options ?? []) as {
    id: string;
    target_atr?: number | null;
  }[];
  const chosen = options.find(
    (o) =>
      o.id ===
      (decision.chosen_option ??
        decision.model_output?.option_id ??
        decision.model_output?.best_option_id),
  );
  if (session?.minutes_left != null) {
    const last = session.last_cycle === true ? ' · last cycle' : '';
    rows.push({
      name: 'Time left',
      detail: `${session.minutes_left} min · target ${
        chosen?.target_atr != null ? `${chosen.target_atr.toFixed(1)} ATR` : '—'
      }${last}`,
      state: 'pass',
    });
  }
  return rows;
}

/** The ev gate's expected value in R: p·rr − (1−p) = p·(rr+1) − 1, with
 * p the model's confidence and rr the chosen option's (docs/SPEC.md,
 * Decision v5 — GUI: "the ev gate row shows confidence against
 * min_confidence and the expected value in R"). */
function expectedValueR(
  decision: DecisionDetail,
  checkValue?: string | null,
): string | null {
  const parsed = checkValue != null ? Number(checkValue) : NaN;
  const confidence = Number.isFinite(parsed)
    ? parsed
    : decision.model_output?.confidence;
  const chosenId =
    decision.chosen_option ??
    decision.model_output?.option_id ??
    decision.model_output?.best_option_id;
  const rr = (decision.options?.options ?? []).find((o) => o.id === chosenId)?.rr;
  if (confidence == null || !Number.isFinite(confidence) || rr == null || rr <= 0) {
    return null;
  }
  const ev = confidence * (rr + 1) - 1;
  return `${ev < 0 ? '−' : '+'}${Math.abs(ev).toFixed(2)}R`;
}

const EV_GATE_PREFIX = 'Confidence ≥ min_confidence';

/** Code checks then risk gates, each pass / fail / reduced. */
export function gateRows(decision: DecisionDetail): GateRow[] {
  const rows: GateRow[] = v5FirstRows(decision);
  for (const check of decision.validation_result?.checks ?? []) {
    const isEvGate = check.name.startsWith(EV_GATE_PREFIX);
    const ev = isEvGate ? expectedValueR(decision, check.value) : null;
    rows.push({
      name: check.name,
      detail:
        [...new Set([check.value, check.detail].filter(Boolean))].join(' · ') +
        (ev !== null ? ` · EV ${ev}` : ''),
      state: check.passed ? 'pass' :
        ['Explanation check', 'Claim checks', 'Confidence sanity', 'Conflicts resolved'].includes(check.name)
          ? 'warning' : 'fail',
    });
  }
  const gates = (decision.gates_result ?? {}) as GatesResult;
  const limitedBy = new Set(gates.sizing?.limited_by ?? []);
  const reducedRisk = Number(gates.risk_factor ?? '1') < 1;
  for (const check of gates.checks ?? []) {
    const reduced =
      check.passed &&
      (limitedBy.has(check.name) ||
        (check.name === 'losing_streak' && reducedRisk));
    rows.push({
      name: GATE_LABELS[check.name] ?? check.name.replace(/_/g, ' '),
      detail:
        check.name === 'reentry' && check.passed
          ? decision.symbol
          : check.detail ?? '',
      state: !check.passed ? 'fail' : reduced ? 'reduced' : 'pass',
    });
  }
  const selection = selectionGateRow(decision);
  if (selection !== null) rows.push(selection);
  return rows;
}

interface ScoredRow {
  id: number;
  symbol: string;
  selection: SelectionRecord;
}

function scoredRows(decision: DecisionDetail): ScoredRow[] {
  const cs = decision.cycle_selection;
  if (!cs || !Array.isArray(cs.scored)) return [];
  return (cs.scored as ScoredRow[])
    .filter((r) => r.selection !== null)
    .sort(
      (a, b) =>
        Number(b.selection.expected_profit) - Number(a.selection.expected_profit),
    );
}

function ordinal(n: number): string {
  const suffix = n === 1 ? 'st' : n === 2 ? 'nd' : n === 3 ? 'rd' : 'th';
  return `${n}${suffix}`;
}

/** The "Selection · expected profit" gate row for a scored candidate. */
export function selectionGateRow(decision: DecisionDetail): GateRow | null {
  const sel = decision.selection;
  if (!sel) return null;
  const rows = scoredRows(decision);
  const pos = rows.findIndex((r) => r.id === decision.id);
  const where = pos >= 0 ? ` · ${ordinal(pos + 1)} of ${rows.length}` : '';
  return {
    name: 'Selection · expected profit',
    detail: `${money(sel.expected_profit) === '—' ? '—' : `$${money(sel.expected_profit)}`}${where} · ${
      sel.chosen ? 'selected' : 'not selected'
    }`,
    state: sel.chosen ? 'pass' : 'reduced',
  };
}

export interface SelectionCycleBlock {
  freeSlots: number;
  scoredCount: number;
  rows: {
    id: number;
    symbol: string;
    rr: string;
    p: string;
    risk: string;
    expectedR: string;
    expectedProfit: string;
    chosen: boolean;
  }[];
  chosenSymbols: string[];
  total: string;
  rankSymbols: string[];
  rankTotal: string;
}

function money(value: string | null | undefined): string {
  if (value == null || value.trim() === '') return '—';
  const n = Number(value);
  return Number.isFinite(n) ? `${n < 0 ? '−' : ''}${Math.abs(n).toFixed(2)}` : '—';
}

/** The "Selection in this cycle" block for the v2/v3 drawers. */
export function selectionCycleBlock(
  decision: DecisionDetail,
): SelectionCycleBlock | null {
  const cs = decision.cycle_selection as CycleSelection | null | undefined;
  if (!cs || cs.mode !== 'expected_profit') return null;
  const rows = scoredRows(decision).map((r) => ({
    id: r.id,
    symbol: r.symbol,
    rr: money(r.selection.rr),
    p: money(r.selection.p),
    risk: money(r.selection.dollars_at_risk),
    expectedR: money(r.selection.expected_r),
    expectedProfit: money(r.selection.expected_profit),
    chosen: r.selection.chosen === true,
  }));
  const chosenSymbols = rows.filter((r) => r.chosen).map((r) => r.symbol);
  const rankChosen = new Set<number>(
    (cs as { rank_chosen?: number[] }).rank_chosen ?? [],
  );
  const rankSymbols = rows
    .filter((r) => rankChosen.has(r.id))
    .map((r) => r.symbol);
  return {
    freeSlots: cs.free_slots ?? 0,
    scoredCount: rows.length,
    rows,
    chosenSymbols,
    total: money(cs.total),
    rankSymbols,
    rankTotal: money(cs.rank_total),
  };
}

export function checkTurnedAdverse(name: string, before: string, current: string): boolean {
  return name === 'momentum'
    ? (before === 'strong' && ['mixed', 'weak'].includes(current)) || (before === 'mixed' && current === 'weak')
    : (before === 'up' && ['mixed', 'down'].includes(current)) || (before === 'mixed' && current === 'down');
}

export function num(value: unknown): number | null {
  return typeof value === 'number' && !Number.isNaN(value) ? value : null;
}

export function signedPct(value: number | null): string {
  if (value === null) return '—';
  return `${value < 0 ? '−' : '+'}${Math.abs(value).toFixed(2)}%`;
}

export function relPct(a: number | null, b: number | null): string {
  if (a === null || b === null || b === 0) return '—';
  return signedPct(((a - b) / b) * 100);
}

/** Signal stack rows: [name, 1h column, daily column]. */
export function signalRows(
  snapshot: Record<string, unknown>,
): [string, string, string][] {
  const ind1h = (snapshot.indicators_1h ?? {}) as Record<string, unknown>;
  const daily = (snapshot.indicators_daily ?? {}) as Record<string, unknown>;
  const price = num(snapshot.price);
  const close = num(daily.close);
  const atr = num(ind1h.atr14);
  const atrPct = atr !== null && price !== null && price > 0 ? (atr / price) * 100 : null;
  const atrRank = num(daily.atr_percentile);
  const rsi = num(ind1h.rsi14);
  return [
    ['RSI 14', rsi !== null ? String(rsi) : '—', '—'],
    ['EMA 20 vs 50', relPct(num(ind1h.ema20), num(ind1h.ema50)), '—'],
    ['vs session VWAP', relPct(price, num(ind1h.vwap)), '—'],
    [
      'vs SMA 50 / 200',
      '—',
      `${relPct(close, num(daily.sma50))} / ${relPct(close, num(daily.sma200))}`,
    ],
    ['OBV trend (20)', String(ind1h.obv_trend ?? '—'), '—'],
    ['RS vs SPY (20)', signedPct(num(ind1h.rs_vs_spy_pct)), '—'],
    [
      'ATR % · rank',
      atrPct !== null ? `${atrPct.toFixed(2)}%` : '—',
      atrRank !== null ? `p${Math.round(atrRank)}` : '—',
    ],
  ];
}

/** The last closed 1h bar end for a decision: the NY :30 grid boundary. */
export function sipBarEnd(cycleTime: string): string {
  const ny = new Date(cycleTime).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'America/New_York',
    hour12: false,
  });
  const [h, m] = ny.split(':').map(Number);
  // Session bars end on the :30 boundary; before :30 the last closed bar
  // ended at the previous hour's :30.
  const hour = m >= 30 ? h : (h + 23) % 24;
  return `${String(hour).padStart(2, '0')}:30`;
}

export function earningsChipLabel(earnings: DecisionEarnings | null): string {
  if (earnings === null || earnings.unavailable) return 'earnings unavailable';
  if (earnings.date === null) return 'no earnings date';
  const date = new Date(`${earnings.date}T00:00:00Z`).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  });
  return `earnings ${date}`;
}

export function firstTouchLabel(touch: string | null | undefined): string | null {
  if (touch === 'stop') return 'stop';
  if (touch === 'target') return 'target';
  if (touch === 'neither') return 'neither yet';
  return null;
}

// Alpaca statuses that mean an order is still live at the broker —
// mirror of backend app.broker.models.OPEN_ORDER_STATUSES.
const OPEN_LIKE = [
  'new',
  'accepted',
  'pending_new',
  'accepted_for_bidding',
  'partially_filled',
  'held',
  'calculated',
  'pending_cancel',
  'pending_replace',
  'pending_review',
];

function seconds(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: 'Europe/Amsterdam',
  });
}

/** The footer's order-status line (design: "Automatic · bracket sent …"). */
export function orderStatusText(decision: DecisionDetail, backtest = false): string {
  const mode = backtest ? 'Backtest' : decision.execution_mode === 'supervised' ? 'Supervised' : 'Automatic';
  const broker = backtest ? 'in backtest' : 'at Alpaca';
  const filled = (order: NonNullable<DecisionDetail['orders']>[number]): string | null => {
    const qty = order.fills.reduce((total, fill) => total + fill.qty, 0);
    if (!qty) return null;
    const price = order.filled_avg_price != null ? Number(order.filled_avg_price)
      : order.fills.reduce((total, fill) => total + Number(fill.price) * fill.qty, 0) / qty;
    const last = order.filled_at ?? order.fills.reduce((latest, fill) =>
      new Date(fill.filled_at) > new Date(latest) ? fill.filled_at : latest, order.fills[0].filled_at);
    return `filled ${qty}${qty < order.qty ? ` of ${order.qty}` : ''} @ ${price.toFixed(2)} at ${seconds(last)}`;
  };
  const orders = decision.orders ?? [];
  const entry = orders.find((o) => o.leg === 'entry');
  const approval = decision.approval;
  const exit = orders.filter((order) => order.leg === 'exit').pop();
  if (exit && (!entry || decision.final_action === 'exit')) {
    return `${mode} · exit ${exit.status} ${broker} · ${filled(exit) ?? `${exit.qty} sh · sent ${seconds(exit.created_at)}`}`;
  }
  if (entry) {
    const parts = [`${mode} · bracket sent ${seconds(entry.created_at)}`];
    const fill = filled(entry);
    if (fill) parts.push(fill);
    parts.push(`entry ${entry.status}`);
    const open = (leg: string) =>
      orders.some((o) => o.leg === leg && OPEN_LIKE.includes(o.status));
    if (open('stop') && open('target')) {
      parts.push(`stop + target open ${broker}`);
    } else if (open('stop')) {
      parts.push(`stop open ${broker}`);
    }
    return parts.join(' · ');
  }
  const other = orders.filter((order) => order.leg !== 'entry' && order.leg !== 'exit').pop();
  if (other) {
    // e.g. a tighten decision records only its replacement stop leg.
    return `${mode} · ${other.leg} ${other.status} ${broker} · ${filled(other) ?? `${other.qty} sh · sent ${seconds(other.created_at)}`}`;
  }
  if (approval) {
    return `${mode} · approval ${approval.status}${
      approval.reason ? ` — ${approval.reason}` : ''
    }`;
  }
  if (decision.final_action === 'no_call') {
    return 'No order — no model call';
  }
  return `${['exit', 'tighten_stop'].includes(decision.final_action) ? 'No order recorded' : 'No order'} — ${actionLabel(decision.final_action).toLowerCase()}`;
}
