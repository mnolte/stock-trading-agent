/** Pure helpers for the decision drawer and the dashboard's latest-decision card. */

import type {
  CycleSelection,
  DecisionDetail,
  DecisionEarnings,
  SelectionRecord,
} from '../types/api';
import { actionLabel } from '../components/decisionFormat';

export type GateState = 'pass' | 'fail' | 'reduced';

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

/** Code checks then risk gates, each pass / fail / reduced. */
export function gateRows(decision: DecisionDetail): GateRow[] {
  const rows: GateRow[] = [];
  for (const check of decision.validation_result?.checks ?? []) {
    rows.push({
      name: check.name,
      detail: check.value ?? check.detail ?? '',
      state: check.passed ? 'pass' : 'fail',
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
    detail: `$${money(sel.expected_profit)}${where} · ${
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
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : '—';
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

export function num(value: unknown): number | null {
  return typeof value === 'number' && !Number.isNaN(value) ? value : null;
}

export function signedPct(value: number | null): string {
  if (value === null) return '—';
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
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
  return `${String(h).padStart(2, '0')}:${m >= 30 ? '30' : '00'}`;
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
export function orderStatusText(decision: DecisionDetail): string {
  const mode = decision.execution_mode === 'supervised' ? 'Supervised' : 'Automatic';
  const orders = decision.orders ?? [];
  const entry = orders.find((o) => o.leg === 'entry');
  const approval = decision.approval;
  if (entry) {
    const parts = [`${mode} · bracket sent ${seconds(entry.created_at)}`];
    const fill = entry.fills[0];
    if (fill) {
      parts.push(`filled ${fill.qty} @ ${fill.price} at ${seconds(fill.filled_at)}`);
    } else {
      parts.push(`entry ${entry.status}`);
    }
    const open = (leg: string) =>
      orders.some((o) => o.leg === leg && OPEN_LIKE.includes(o.status));
    if (open('stop') && open('target')) {
      parts.push('stop + target open at Alpaca');
    } else if (open('stop')) {
      parts.push('stop open at Alpaca');
    }
    return parts.join(' · ');
  }
  if (approval) {
    return `${mode} · approval ${approval.status}${
      approval.reason ? ` — ${approval.reason}` : ''
    }`;
  }
  if (decision.final_action === 'no_call') {
    return 'No order — no model call';
  }
  return `No order — ${actionLabel(decision.final_action).toLowerCase()}`;
}
