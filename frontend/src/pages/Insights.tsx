import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import { get } from '../api/client';
import { useApiResource } from '../api/useResource';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import { SegmentedControl } from '../components/SegmentedControl';
import { StatCard } from '../components/StatCard';
import { ErrorBanner } from '../components/cardState';
import {
  SkeletonList,
  SkeletonTable,
  SkeletonText,
  SkeletonTiles,
} from '../components/skeletons';
import {
  displayDayTime,
  displayTime,
  sessionDayShort,
  signedPct,
} from '../format';
import { EXIT_REASONS, REASON_CODES, labelFor } from '../labels';
import { ghostButton } from '../components/errorPageStyles';

/** One insights snapshot as the API serves it (docs/SPEC.md, Insights).
 * `needs`/`have` are the sample states item 075 adds: a number that
 * needs more trades carries how many it needs and how many there are. */

interface Sampled {
  needs?: number;
  have?: number | null;
}

interface Finding {
  dimension: string;
  dimension_label: string;
  slice: string;
  status: 'finding' | 'early';
  trades: number;
  avg_r: string | null;
  rest_trades: number;
  rest_avg_r: string | null;
  sigma: string | null;
  total_r: string | null;
  first_seen?: string;
  held_days?: number;
  new?: boolean;
}

interface SliceRow {
  slice: string;
  sub?: string | null;
  trades?: number;
  win_rate_pct?: string | null;
  avg_r?: string | null;
  lo?: string | null;
  hi?: string | null;
  total_r?: string | null;
  skips?: number;
  skips_missed_pct?: string | null;
  skips_avoided_pct?: string | null;
  status?: string;
  rule?: string;
  note?: string;
}

interface Snapshot {
  session_date: string;
  computed_at: string;
  trades: number;
  /** The five answer sentences, computed with the snapshot (item 075). */
  answers?: Partial<Record<'edge' | 'stands_out' | 'trades_lost' | 'conditions' | 'costs', string>>;
  headline: {
    expectancy: { avg_r: string | null; lo: string | null; hi: string | null; n: number } & Sampled;
    edge_over_spy: { pts: string | null; return_pct: string | null; spy_return_pct: string | null; exposure: string | null } & Sampled;
    beta: { beta: string | null; correlation: string | null; sessions: number } & Sampled;
    overnight: { in_session_usd: string | null; overnight_usd: string | null };
    gaps: { count: number; extra_r: string | null };
    model_cost: { total_usd: string | null; per_trade_usd: string | null; pct_gross_profit: string | null } & Sampled;
  };
  funnel: { steps: { key: string; count: number; losses: { label: string; count: number }[] }[] };
  findings: Finding[];
  skipped: {
    total: number; scored?: number; missed?: number; avoided?: number; neither?: number;
    missed_pct?: string | null; avoided_pct?: string | null; neither_pct?: string | null;
    baseline_r?: string | null; not_filled?: number; na?: string;
  };
  slices: Record<string, { label: string; rows: SliceRow[]; hidden: number }>;
  /** What each filter removed (docs/SPEC.md, Decision v5): per filter,
   * the options it removed and their counterfactual touches. */
  filters_removed?: ({
    filter: string;
    count: number;
    decided: number;
    target_first: number;
    stop_first: number;
    neither: number;
    r_made: string | null;
  } & Sampled)[];
  calibration: {
    buckets?: { label: string; stated: string | null; actual: string | null; n: number; status: string }[];
    hidden?: number; entries?: number; weighted_gap_pts?: string | null; na?: string;
    min_entries?: number;
  } & Sampled;
  execution: {
    fill_rate: string | null; filled: number; placed: number;
    worst_cycle: { cycle: string; rate: number; filled: number; placed: number } | null;
    entry_slippage_bps: string | null; exit_slippage_bps: string | null;
    exit_slippage_splits: { reason: string; bps: string | null; n: number }[];
    gaps_count: number; gaps_extra_r: string | null;
    same_day_exits: number; same_day_avg_r: string | null;
    earnings_exits: number; earnings_avg_r: string | null; earnings_median_move_pct: string | null;
    dividends_usd: string | null; dividend_positions: number;
  };
  overnight: {
    in_session_usd: string | null; overnight_usd: string | null;
    nights: number; gapped_against: number; gapped_pct: string | null;
    largest_gap: { r: string; symbol: string; date: string } | null;
  };
}

/** The latest cycle's sector table + breadth for the Sectors now card
 * (item 068) — live data, not part of the stored snapshot. */
interface SectorsNow {
  at: string;
  breadth: {
    above_sma50_pct: number | null;
    above_sma200_pct: number | null;
    ema_up_pct: number | null;
    up_today_pct: number | null;
  } | null;
  sectors: {
    rank: number | null;
    etf: string | null;
    sector: string;
    rs_vs_spy_pct: number | null;
  }[];
}

interface InsightsResponse {
  strategies: { id: string; name: string; kind: string }[];
  period: string;
  empty: boolean;
  sectors_now?: SectorsNow | null;
  snapshot: Snapshot | null;
}

interface InsightTrade {
  id: number;
  symbol: string;
  qty: number;
  entry: string | null;
  opened_at: string | null;
  closed_at: string | null;
  exit_reason: string | null;
  pnl: string | null;
  r: string | null;
  decision_id: number | null;
}

interface InsightTradesResponse {
  trades: InsightTrade[];
  slice: string;
  dimension: string;
  empty?: boolean;
}

const PERIODS: [string, string][] = [
  ['run', 'This run'],
  ['last20', 'Last 20 sessions'],
  ['all', 'All runs'],
];

const FUNNEL_LABELS: Record<string, string> = {
  symbol_checks: 'Symbol checks',
  rules: 'Passed the rules pre-filter',
  called: 'Model called for a candidate',
  proposed: 'Model proposed an entry',
  code_checks: 'Passed code checks',
  risk_gates: 'Passed risk gates',
  filled: 'Filled',
  closed: 'Closed',
};

/** The "Slice by" select groups (design/screens/Insights.dc.html): the
 * 21 dimensions under Timing / Market / Stock / Model. Only dimensions
 * present in the snapshot get an option. */
const DIM_GROUPS: [string, string[]][] = [
  ['Timing', ['entry_cycle', 'weekday', 'sessions_held']],
  [
    'Market',
    [
      'regime',
      'spy_that_day',
      'vix_level',
      'vix_change_5d',
      'breadth',
      'release_next_morning',
    ],
  ],
  [
    'Stock',
    [
      'sector',
      'symbol',
      'sector_rank',
      'stock_vs_sector',
      'days_to_earnings',
      'opening_gap',
      'entry_vs_vwap',
      'pullback_depth',
      'strength_vs_spy',
      'volatility',
    ],
  ],
  ['Model', ['confidence', 'stop_target_basis', 'prompt_model']],
];

/** "Test in a shadow" applies only to the dimensions that map to a
 * strategy override — today that's just the entry cycle. */
const SHADOWABLE: Record<string, (slice: string) => Record<string, unknown>> = {
  entry_cycle: (slice) => ({ entry_cycles_excluded: slice }),
};

function fmtR(v: string | null | undefined): string {
  if (v == null) return '—';
  const n = Number(v);
  const sign = n > 0 ? '+' : n < 0 ? '−' : '';
  return `${sign}${Math.abs(n).toFixed(2)}R`;
}
function fmtUsd(v: string | null | undefined): string {
  if (v == null) return '—';
  const n = Number(v);
  const sign = n > 0 ? '+' : n < 0 ? '−' : '';
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}
function fmtPct(v: string | null | undefined): string {
  return v == null ? '—' : `${Number(v).toFixed(1)}%`;
}
function fmtPct0(v: string | null | undefined): string {
  return v == null ? '—' : `${Math.round(Number(v))}%`;
}

/** "needs 10 closed trades · 4 so far" — the sample line a tile or card
 * shows instead of a dash while its numbers are still too small
 * (docs/SPEC.md → Insights → GUI — Answers, not dashes). */
function sampleLine(
  node: Sampled | undefined,
  unit: string,
): string | null {
  if (!node || node.needs == null || node.have == null) return null;
  if (node.have >= node.needs) return null;
  return `needs ${node.needs} ${unit} · ${node.have} so far`;
}

function breadthNote(
  breadth: SectorsNow['breadth'],
): string {
  if (!breadth) return '';
  const parts: string[] = [];
  if (breadth.above_sma50_pct != null) {
    parts.push(
      `${Math.round(breadth.above_sma50_pct)}% of the universe above SMA 50`,
    );
  }
  if (breadth.up_today_pct != null) {
    parts.push(`${Math.round(breadth.up_today_pct)}% up today`);
  }
  return parts.length ? ` Breadth then: ${parts.join(', ')}.` : '';
}

function RangeBar({ lo, hi, avg }: { lo: string | null | undefined; hi: string | null | undefined; avg: string | null | undefined }) {
  if (lo == null || hi == null || avg == null) return <span>—</span>;
  const loN = Number(lo); const hiN = Number(hi); const avgN = Number(avg);
  const span = Math.max(Math.abs(loN), Math.abs(hiN), 0.1);
  const pct = (v: number) => `${(v / (2 * span) + 0.5) * 100}%`;
  return (
    <div role="img" aria-label={`range ${loN.toFixed(2)} to ${hiN.toFixed(2)}`} style={{ position: 'relative', height: 6, background: 'var(--sa-raised)', borderRadius: 3, minWidth: 60 }}>
      <div style={{ position: 'absolute', left: pct(loN), width: `calc(${pct(hiN)} - ${pct(loN)})`, height: '100%', background: 'var(--sa-accent)', opacity: 0.4, borderRadius: 3 }} />
      <div style={{ position: 'absolute', left: pct(avgN), width: 2, height: '100%', background: 'var(--sa-text)' }} />
    </div>
  );
}

interface FilterRemovedRow {
  filter: string;
  count: number;
  decided: number;
  target_first: number;
  stop_first: number;
  neither: number;
  r_made: string | null;
  needs?: number;
  have?: number | null;
}

/** Display names for the filter keys the backend groups under
 * (docs/SPEC.md, Decision v5 — What each filter removed). */
const FILTER_LABELS: Record<string, string> = {
  room: 'Room to first level',
  'rr cap': 'Reward/risk cap',
  'trend gate': 'Trend gate',
  'confidence gate': 'Expected-value gate',
  'sideways rule': 'Sideways rule',
  spread: 'Spread',
  slots: 'Slots full · not selected',
  rules: 'Rules',
  checks: 'Code checks',
  fomc_window: 'FOMC window',
};

function filterLabel(key: string): string {
  return FILTER_LABELS[key] ?? key.replace(/_/g, ' ');
}

const thStyle: React.CSSProperties = {
  textAlign: 'right',
  padding: '8px 10px',
  fontSize: 'var(--sa-text-sm)',
  fontWeight: 500,
  color: 'var(--sa-text-muted)',
  borderBottom: '1px solid var(--sa-border)',
  whiteSpace: 'nowrap',
};
const tdStyle: React.CSSProperties = {
  padding: '9px 10px',
  borderBottom: '1px solid var(--sa-border)',
  fontSize: 'var(--sa-text-md)',
};
const monoTd: React.CSSProperties = {
  ...tdStyle,
  fontFamily: 'var(--sa-font-mono)',
  textAlign: 'right',
  whiteSpace: 'nowrap',
};
const mutedNote: React.CSSProperties = {
  fontSize: 'var(--sa-text-sm)',
  color: 'var(--sa-text-muted)',
};

/** "What each filter removed" — the card between the funnel and the
 * slices (design/screens/Insights.dc.html). Percentages are of the
 * decided counterfactual touches; under 30 decided the cells say what
 * they need instead of a dash, under 100 the R column says "needs 100". */
function FilterRemovedCard({ filters, periodLabel, refreshing }: { filters: FilterRemovedRow[]; periodLabel: string; refreshing?: boolean }) {
  const total = filters.reduce((n, f) => n + f.count, 0);
  const badge = (row: FilterRemovedRow): { text: string; accent: boolean } =>
    row.decided < 30
      ? { text: 'too few', accent: false }
      : row.decided < 100
        ? { text: 'early · under 100 decided', accent: false }
        : { text: 'counts', accent: true };
  return (
    <Card
      label="What each filter removed"
      refreshing={refreshing}
      right={`${periodLabel.toLowerCase()} · ${total.toLocaleString()} candidates stopped before the model · decided touches only`}
    >
      {filters.length === 0 ? (
        <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
          No counterfactual touches yet — the card fills once decisions with option touches fill in.
        </div>
      ) : (
        <>
          <div style={{ overflowX: 'auto', minWidth: 0 }}>
            <table style={{ width: '100%', minWidth: '760px', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th scope="col" style={{ ...thStyle, textAlign: 'left' }}>Filter</th>
                  <th scope="col" style={thStyle}>R if taken, avg</th>
                  <th scope="col" style={thStyle}>Removed</th>
                  <th scope="col" style={thStyle}>Target first</th>
                  <th scope="col" style={thStyle}>Stop first</th>
                  <th scope="col" style={thStyle}>Neither</th>
                  <th scope="col" style={{ ...thStyle, textAlign: 'left' }}></th>
                </tr>
              </thead>
              <tbody>
                {filters.map((row) => {
                  const b = badge(row);
                  const few = row.decided < 30;
                  const early = row.decided < 100;
                  const pct = (n: number) =>
                    row.decided > 0 ? `${Math.round((n / row.decided) * 100)}%` : '—';
                  // "Neither" covers every decided touch that was not a
                  // target or stop first (incl. not_filled).
                  const neither = Math.max(0, row.decided - row.target_first - row.stop_first);
                  const r = row.r_made !== null ? Number(row.r_made) : null;
                  return (
                    <tr key={row.filter}>
                      <td style={tdStyle}>{filterLabel(row.filter)}</td>
                      <td style={{ ...monoTd, color: early || r === null ? 'var(--sa-text-muted)' : r >= 0 ? 'var(--sa-positive)' : 'var(--sa-negative-text)' }}>
                        {early || r === null
                          ? `needs 100 · ${row.decided} so far`
                          : `${r >= 0 ? '+' : '−'}${Math.abs(r).toFixed(2)}R`}
                      </td>
                      <td style={{ ...monoTd, color: 'var(--sa-text-2)' }}>
                        {row.count.toLocaleString()}
                      </td>
                      <td style={{ ...monoTd, color: few ? 'var(--sa-text-muted)' : 'var(--sa-positive)' }}>
                        {few ? 'needs 30' : pct(row.target_first)}
                      </td>
                      <td style={{ ...monoTd, color: few ? 'var(--sa-text-muted)' : 'var(--sa-negative-text)' }}>
                        {few ? 'needs 30' : pct(row.stop_first)}
                      </td>
                      <td style={{ ...monoTd, color: few ? 'var(--sa-text-muted)' : 'var(--sa-text-muted)' }}>
                        {few ? 'needs 30' : pct(neither)}
                      </td>
                      <td style={tdStyle}>
                        <Chip variant={b.accent ? 'warning' : 'neutral'}>{b.text}</Chip>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ ...mutedNote, lineHeight: 1.5 }}>
            Every candidate a filter stopped gets the options code would have built
            and their touches (counterfactual touches), so each filter shows what
            it removed. "R if taken" is the average over decided touches: target
            +R/R, stop −1R. A filter that removes setups with a positive R costs
            money; one that removes negative R earns its keep.
          </div>
        </>
      )}
    </Card>
  );
}

const findingButton: React.CSSProperties = {
  ...ghostButton,
  height: 'auto',
  minHeight: 36,
  padding: '8px 16px',
  fontSize: 'var(--sa-text-md)',
  whiteSpace: 'normal',
  textAlign: 'left',
};
const findingPrimaryButton: React.CSSProperties = {
  ...findingButton,
  border: '1px solid var(--sa-border-strong)',
  background: 'var(--sa-raised)',
  color: 'var(--sa-text)',
};

const DEFS: Record<string, string> = {
  expectancy: 'Average adjusted R per closed trade. The band is the 90% range: ± 1.64 standard errors on the mean.',
  edge: 'Run return minus SPY’s return over the same sessions, at the strategy’s average equity exposure.',
  beta: 'Slope and correlation of daily equity returns against SPY over 20 sessions.',
  overnight: 'P&L of held positions from the previous close to the open, versus from the open to the close or exit.',
  gaps: 'Stop exits that filled more than 0.1R below the stop, and the extra R lost beyond −1R.',
  cost: 'Decision-model cost in the period, per closed trade and as a share of gross profit.',
};

const tileLabel = (label: string, def: string) => (
  <span title={def} style={{ borderBottom: '1px dotted var(--sa-text-muted)', cursor: 'help', alignSelf: 'flex-start' }}>{label}</span>
);

/** A headline tile: the four numbers of question 1. While a number needs
 * more trades the tile shows the sample line and its definition instead
 * of a dash (item 075). */
function InsightTile({
  label,
  def,
  value,
  valueColor,
  sub,
  sample,
}: {
  label: string;
  def: string;
  value: string;
  valueColor?: string;
  sub?: string;
  sample: string | null;
}) {
  if (sample) {
    return (
      <div
        style={{
          flex: '1 1 200px',
          padding: 'var(--sa-space-4)',
          background: 'var(--sa-panel)',
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-lg)',
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
          minWidth: 0,
        }}
      >
        {tileLabel(label, def)}
        <div style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
          {sample}
        </div>
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.4 }}>{def}</div>
      </div>
    );
  }
  return (
    <StatCard
      label={tileLabel(label, def)}
      value={value}
      valueColor={valueColor}
      sub={sub}
    />
  );
}

/** A question's header: number, title, optional meta and the answer
 * sentence (design/screens/Insights.dc.html). */
function Question({
  id,
  n,
  title,
  meta,
  answer,
}: {
  id: string;
  n: number;
  title: string;
  meta?: React.ReactNode;
  answer?: string;
}) {
  return (
    <>
      <span id={id} style={{ display: 'block', height: 0, scrollMarginTop: 16 }} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, paddingTop: 'var(--sa-space-3)' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', justifyContent: 'space-between', gap: '6px 12px' }}>
          <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>
            <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)', marginRight: 8 }}>{n}</span>
            {title}
          </h2>
          {meta}
        </div>
        {answer && (
          <p style={{ margin: 0, fontSize: 'var(--sa-text-md)', lineHeight: 1.55, color: 'var(--sa-text-2)', maxWidth: 900 }}>
            {answer}
          </p>
        )}
      </div>
    </>
  );
}

/** A label/value row inside the question-5 cards (design's execution
 * and overnight rows). */
function Fact({
  label,
  note,
  children,
  color,
}: {
  label: string;
  note?: string;
  children: React.ReactNode;
  color?: string;
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '9px 0', borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)' }}>
      <span style={{ color: 'var(--sa-text-2)' }}>
        {label}
        {note && <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}> · {note}</span>}
      </span>
      <span style={{ fontFamily: 'var(--sa-font-mono)', color: color ?? 'var(--sa-text)', whiteSpace: 'nowrap' }}>{children}</span>
    </div>
  );
}

const ON_THIS_PAGE: [string, string][] = [
  ['q1', '1 Edge'],
  ['q2', '2 Stands out'],
  ['q3', '3 Trades lost'],
  ['q4', '4 Conditions'],
  ['q5', '5 Costs'],
];

export default function Insights() {
  const [strategy, setStrategy] = useState('live');
  const [period, setPeriod] = useState('run');
  const [tradeSlice, setTradeSlice] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const res = useApiResource<InsightsResponse>(
    (signal) =>
      get<InsightsResponse>(
        `/api/insights?strategy=${strategy}&period=${period}`,
        signal,
      ),
    [strategy, period],
  );
  const strategyKind = res.data?.strategies.find((s) => s.id === strategy)?.kind;
  const snap = res.data?.snapshot ?? null;
  // The slice dimension lives in the URL (?dim=); anything the snapshot
  // doesn't have falls back to the entry cycle.
  const dimParam = searchParams.get('dim');
  const dimension =
    dimParam && snap?.slices && dimParam in snap.slices ? dimParam : 'entry_cycle';
  const tradesRes = useApiResource<InsightTradesResponse>(
    (signal) =>
      get<InsightTradesResponse>(
        `/api/insights/trades?strategy=${strategy}&period=${period}&dimension=${dimension}&slice=${encodeURIComponent(tradeSlice ?? '')}`,
        signal,
      ),
    [strategy, period, dimension, tradeSlice],
    tradeSlice !== null,
  );
  const setDimension = (key: string) =>
    setSearchParams({ dim: key }, { replace: true });
  const showTrades = (dim: string, slice: string) => {
    setDimension(dim);
    setTradeSlice(slice);
  };
  const dim = snap?.slices?.[dimension];
  const periodLabel = PERIODS.find(([k]) => k === period)?.[1] ?? 'This run';
  const answers = snap?.answers ?? {};

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-4)', padding: 'var(--sa-space-4)' }}>
      <h1 style={{ margin: 0, fontSize: 'var(--sa-text-xl)' }}>Insights</h1>

      {/* filters */}
      <section
        aria-label="Filters"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '14px 24px',
          alignItems: 'flex-end',
          background: 'var(--sa-panel)',
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-lg)',
          padding: 'var(--sa-space-4) var(--sa-space-5)',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>Strategy</span>
          <SegmentedControl
            label="Strategy"
            options={(res.data?.strategies ?? [{ id: 'live', name: 'Live' }]).map((s) => s.name)}
            value={(res.data?.strategies ?? []).find((s) => s.id === strategy)?.name ?? 'Live'}
            onChange={(name) => {
              const s = (res.data?.strategies ?? []).find((x) => x.name === name);
              if (s) setStrategy(s.id);
            }}
          />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>Period</span>
          <SegmentedControl
            label="Period"
            options={PERIODS.map(([, label]) => label)}
            value={PERIODS.find(([k]) => k === period)?.[1] ?? 'This run'}
            onChange={(label) => setPeriod(PERIODS.find(([, l]) => l === label)?.[0] ?? 'run')}
          />
        </div>
        <span style={{ flex: 1 }} />
        {snap && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', textAlign: 'right' }}>
            <span>Computed after the close · {sessionDayShort(snap.session_date)} {displayTime(snap.computed_at)}</span>
            <span>{snap.trades.toLocaleString()} closed trades · adjusted P&amp;L</span>
          </div>
        )}
      </section>

      {res.failure && res.data === null && (
        <ErrorBanner
          what="insights"
          failure={res.failure}
          updatedAt={res.updatedAt}
          onRetry={res.reload}
        />
      )}
      {res.data?.empty && !res.loading && <Card><div>No insights snapshot yet — the daily job writes one after each session close.</div></Card>}

      {snap && (
        <>
          {/* on this page — the five questions, at every width */}
          <nav aria-label="On this page" style={{ display: 'flex', gap: 'var(--sa-space-2)', overflowX: 'auto', paddingBottom: 2, alignItems: 'center' }}>
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', whiteSpace: 'nowrap' }}>On this page</span>
            {ON_THIS_PAGE.map(([id, label]) => (
              <a
                key={id}
                href={`#${id}`}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  height: 36,
                  padding: '0 var(--sa-space-3)',
                  borderRadius: 18,
                  border: '1px solid var(--sa-border)',
                  background: 'var(--sa-raised)',
                  color: 'var(--sa-text-2)',
                  fontSize: 'var(--sa-text-md)',
                  textDecoration: 'none',
                  whiteSpace: 'nowrap',
                }}
              >
                {label}
              </a>
            ))}
          </nav>

          {/* how to read this page */}
          <details style={{ background: 'var(--sa-panel)', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-lg)', padding: 'var(--sa-space-3) var(--sa-space-5)' }}>
            <summary style={{ cursor: 'pointer', minHeight: 32, display: 'flex', alignItems: 'center', fontSize: 'var(--sa-text-md)', fontWeight: 600 }}>
              ▸ How to read this page
            </summary>
            <dl style={{ margin: '10px 0 4px', display: 'grid', gridTemplateColumns: 'minmax(120px, 180px) minmax(0, 1fr)', gap: '10px 18px', fontSize: 'var(--sa-text-md)', lineHeight: 1.5 }}>
              <dt style={{ color: 'var(--sa-text)', fontWeight: 600 }}>R</dt>
              <dd style={{ margin: 0, color: 'var(--sa-text-2)' }}>What a trade risks: entry minus stop, times the shares. +2R earned twice what it risked; a stop-out is about −1R.</dd>
              <dt style={{ color: 'var(--sa-text)', fontWeight: 600 }}>Expectancy</dt>
              <dd style={{ margin: 0, color: 'var(--sa-text-2)' }}>The average R per closed trade. Above 0 means the trades earn more than they lose, on average.</dd>
              <dt style={{ color: 'var(--sa-text)', fontWeight: 600 }}>90% range</dt>
              <dd style={{ margin: 0, color: 'var(--sa-text-2)' }}>Where the true average probably lies, given how few trades there are. A range that includes 0 means the edge isn't proven yet.</dd>
              <dt style={{ color: 'var(--sa-text)', fontWeight: 600 }}>Finding, early signal</dt>
              <dd style={{ margin: 0, color: 'var(--sa-text-2)' }}>A finding has 30 or more trades and a difference at least twice the noise. An early signal has 10–29 trades; it is greyed and not worth acting on yet.</dd>
              <dt style={{ color: 'var(--sa-text)', fontWeight: 600 }}>A finding is a question</dt>
              <dd style={{ margin: 0, color: 'var(--sa-text-2)' }}>Checking many slices finds some differences by chance. Test a finding in a shadow or a backtest before changing anything.</dd>
            </dl>
          </details>

          {/* 1 · Is there an edge? */}
          <Question
            id="q1"
            n={1}
            title="Is there an edge?"
            meta={<span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{periodLabel.toLowerCase()} · {snap.trades.toLocaleString()} closed trades</span>}
            answer={answers.edge}
          />
          <section aria-label="Headline numbers" className="sa-tiles sa-tiles-4">
            <InsightTile
              label="Expectancy"
              def={DEFS.expectancy}
              value={fmtR(snap.headline.expectancy.avg_r)}
              valueColor={snap.headline.expectancy.avg_r != null && Number(snap.headline.expectancy.avg_r) >= 0 ? 'var(--sa-positive)' : 'var(--sa-negative-text)'}
              sub={snap.headline.expectancy.lo != null ? `90% range ${fmtR(snap.headline.expectancy.lo)} to ${fmtR(snap.headline.expectancy.hi)} · ${snap.headline.expectancy.n} trades` : undefined}
              sample={snap.headline.expectancy.avg_r == null ? sampleLine(snap.headline.expectancy, 'closed trades') : null}
            />
            <InsightTile
              label="Edge over SPY at the same exposure"
              def={DEFS.edge}
              value={snap.headline.edge_over_spy.pts != null ? `${Number(snap.headline.edge_over_spy.pts) >= 0 ? '+' : ''}${snap.headline.edge_over_spy.pts} pts` : '—'}
              valueColor={snap.headline.edge_over_spy.pts != null && Number(snap.headline.edge_over_spy.pts) >= 0 ? 'var(--sa-positive)' : 'var(--sa-negative-text)'}
              sub={`run ${fmtPct(snap.headline.edge_over_spy.return_pct)} vs SPY ${fmtPct(snap.headline.edge_over_spy.spy_return_pct)} × ${snap.headline.edge_over_spy.exposure ?? '—'} exposure`}
              sample={snap.headline.edge_over_spy.pts == null ? sampleLine(snap.headline.edge_over_spy, 'closed trades') : null}
            />
            <InsightTile
              label="Beta to SPY"
              def={DEFS.beta}
              value={snap.headline.beta.beta != null ? snap.headline.beta.beta : '—'}
              sub={snap.headline.beta.beta != null ? `correlation ${snap.headline.beta.correlation ?? '—'} · daily returns, ${snap.headline.beta.sessions} sessions` : undefined}
              sample={snap.headline.beta.beta == null ? sampleLine(snap.headline.beta, 'sessions') : null}
            />
            <InsightTile
              label="Model cost per trade"
              def={DEFS.cost}
              value={fmtUsd(snap.headline.model_cost.per_trade_usd)}
              sub={snap.headline.model_cost.pct_gross_profit != null ? `${snap.headline.model_cost.pct_gross_profit}% of gross profit · ${fmtUsd(snap.headline.model_cost.total_usd)} total` : fmtUsd(snap.headline.model_cost.total_usd)}
              sample={snap.headline.model_cost.per_trade_usd == null ? sampleLine(snap.headline.model_cost, 'closed trades') : null}
            />
          </section>

          {/* 2 · What stands out? */}
          <Question id="q2" n={2} title="What stands out?" answer={answers.stands_out} />
          <div className="sa-btnwrap">
            <Card
              label="Findings and early signals"
              refreshing={res.refreshing}
              right={`${snap.findings.length} in this period${snap.findings.some((f) => f.new) ? ` · ${snap.findings.filter((f) => f.new).length} new` : ''}`}
            >
              {snap.findings.length === 0 && <div style={{ color: 'var(--sa-text-muted)' }}>No findings in this period. Nothing differed by more than two standard errors on a big enough sample.</div>}
              {snap.findings.map((f) => (
                <div key={`${f.dimension}|${f.slice}`} style={{ border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', padding: 'var(--sa-space-3)', opacity: f.status === 'early' ? 0.65 : 1, display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-2)' }}>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-2)', alignItems: 'center' }}>
                    <Chip variant={f.status === 'finding' ? 'accent' : 'neutral'} wrap>
                      {f.status === 'finding' ? 'finding · 30+ trades' : 'early signal · 10–29 trades'}
                    </Chip>
                    <b style={{ fontWeight: 600, fontSize: 'var(--sa-text-md)' }}>
                      {f.dimension_label}: {f.slice}
                      {f.new && <span style={{ color: 'var(--sa-accent)' }}> · new</span>}
                    </b>
                  </div>
                  <div style={{ color: 'var(--sa-text-2)', fontSize: 'var(--sa-text-md)', lineHeight: 1.5 }}>
                    {f.trades} trades at {fmtR(f.avg_r)} vs {fmtR(f.rest_avg_r)} for the other {f.rest_trades} — {f.sigma}× the noise{f.status === 'early' ? ' · too few trades to act on; it becomes a finding at 30' : ''}{f.held_days ? ` · held for ${f.held_days} day${f.held_days === 1 ? '' : 's'}` : ''}
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-2)' }}>
                    {SHADOWABLE[f.dimension] && f.status === 'finding' && (
                      <button type="button" style={findingPrimaryButton} onClick={() => navigate(`/strategies?new_shadow=1&exclude_cycle=${encodeURIComponent(f.slice)}`)}>Test in a shadow</button>
                    )}
                    <button type="button" style={findingButton} onClick={() => showTrades(f.dimension, f.slice)}>Show the trades</button>
                  </div>
                </div>
              ))}
              <div style={{ ...mutedNote, lineHeight: 1.5 }}>
                A finding needs 30 trades in the slice and a difference of at least twice the noise against the rest. Findings are never applied automatically: test one in a shadow or a backtest first. Checking many slices finds some differences by chance, so a finding is a question, not a rule.
              </div>
            </Card>
          </div>

          {/* 3 · Where are trades lost? */}
          <Question id="q3" n={3} title="Where are trades lost?" answer={answers.trades_lost} />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'flex-start' }}>
            <Card label="Where trades come from" refreshing={res.refreshing} style={{ flex: '1 1 520px' }}>
              {(() => {
                const steps = snap.funnel.steps;
                const first = steps[0]?.count ?? 0;
                const maxLog = first > 0 ? Math.log10(first) : 1;
                return steps.map((s, i) => {
                  const prev = i > 0 ? steps[i - 1].count : null;
                  const kept = prev != null && prev > 0 ? Math.round((s.count / prev) * 100) : null;
                  const width = first > 0 ? Math.max(2, (Math.log10(Math.max(s.count, 1)) / maxLog) * 100) : 0;
                  const losses = s.losses.filter((l) => l.count > 0);
                  return (
                    <div key={s.key} className="sa-funnel">
                      <span className="sa-funnel-name" style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>{FUNNEL_LABELS[s.key] ?? s.key}</span>
                      <div className="sa-funnel-bar" style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
                        <div style={{ height: 8, borderRadius: 4, background: 'var(--sa-raised)' }}>
                          <div style={{ width: `${width}%`, height: 8, borderRadius: 4, background: s.key === 'closed' ? 'var(--sa-accent)' : 'var(--sa-info)' }} />
                        </div>
                        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                          {kept != null && <span style={{ fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text-2)' }}>{kept}% kept</span>}
                          {kept != null && losses.length > 0 && ' · '}
                          {losses.map((l, j) => (
                            <span key={l.label}>{j > 0 ? ' · ' : ''}lost: {l.label} {l.count.toLocaleString()}</span>
                          ))}
                        </span>
                      </div>
                      <span className="sa-funnel-n" style={{ fontSize: 'var(--sa-text-md)' }}>{s.count.toLocaleString()}</span>
                    </div>
                  );
                });
              })()}
              <div style={{ ...mutedNote, lineHeight: 1.5 }}>
                "Kept" is the share of the step before. Bar length on a log scale.
              </div>
            </Card>
            <Card label="Skipped setups" refreshing={res.refreshing} style={{ flex: '1 1 380px' }}>
              {snap.skipped.na ? (
                <div style={{ color: 'var(--sa-text-muted)' }}>{snap.skipped.na}</div>
              ) : (
                <>
                  <Fact label="Model skips" note="candidates the model passed on">{snap.skipped.total.toLocaleString()}</Fact>
                  <Fact label="Would have reached the target first" note="missed" color="var(--sa-warning-text)">{fmtPct0(snap.skipped.missed_pct)}</Fact>
                  <Fact label="Would have hit the stop first" note="avoided" color="var(--sa-positive)">{fmtPct0(snap.skipped.avoided_pct)}</Fact>
                  <Fact label="Neither within the horizon">{fmtPct0(snap.skipped.neither_pct)}</Fact>
                  <Fact label="Not filled" note="the limit was never touched">{snap.skipped.not_filled ?? 0}</Fact>
                  <Fact label="Rules-only baseline on the same setups" note="what taking every skip would have earned" color={snap.skipped.baseline_r != null && Number(snap.skipped.baseline_r) >= 0 ? 'var(--sa-positive)' : 'var(--sa-negative-text)'}>
                    {fmtR(snap.skipped.baseline_r ?? null)} avg
                  </Fact>
                  <div style={{ ...mutedNote, lineHeight: 1.5 }}>
                    Skips are scored with the rules-only geometry (stop 2 × ATR, target 2R, 5 sessions); a skip that would have hit its stop first was avoided, one that reached the target was missed.
                  </div>
                </>
              )}
            </Card>
          </div>

          <FilterRemovedCard filters={snap.filters_removed ?? []} periodLabel={periodLabel} refreshing={res.refreshing} />

          {/* 4 · Which conditions work? */}
          <Question id="q4" n={4} title="Which conditions work?" answer={answers.conditions} />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'flex-start' }}>
            <Card
              label="Slices"
              refreshing={res.refreshing}
              style={{ flex: '1 1 640px' }}
              right={
                <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
                  by {dim?.label.toLowerCase() ?? 'slice'} · {snap.trades.toLocaleString()} trades
                </span>
              }
            >
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '10px 16px' }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                  Slice by
                  <select
                    aria-label="Dimension"
                    value={dimension}
                    onChange={(e) => setDimension(e.target.value)}
                    style={{ height: 40, boxSizing: 'border-box', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border-strong)', background: 'var(--sa-bg)', color: 'var(--sa-text)', fontSize: 'var(--sa-text-md)', padding: '0 10px', minWidth: 260 }}
                  >
                    {DIM_GROUPS.map(([group, keys]) => {
                      const options = keys.filter((k) => snap.slices[k]);
                      if (options.length === 0) return null;
                      return (
                        <optgroup key={group} label={group}>
                          {options.map((k) => (
                            <option key={k} value={k}>{snap.slices[k].label}</option>
                          ))}
                        </optgroup>
                      );
                    })}
                    {/* dims the grouping doesn't know still get a flat tail */}
                    {Object.keys(snap.slices).filter((k) => !DIM_GROUPS.some(([, keys]) => keys.includes(k))).map((k) => (
                      <option key={k} value={k}>{snap.slices[k].label}</option>
                    ))}
                  </select>
                </label>
                <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', paddingBottom: 10 }}>
                  {dim ? `${dim.label}: each row is compared with all other trades.` : ''}
                </span>
              </div>
              {dim && (
                <>
                  <div style={{ overflowX: 'auto', minWidth: 0 }}>
                    <table style={{ width: '100%', minWidth: '860px', borderCollapse: 'collapse', fontSize: 'var(--sa-text-md)' }}>
                      <thead>
                        <tr style={{ color: 'var(--sa-text-muted)', textAlign: 'left' }}>
                          <th style={{ ...thStyle, textAlign: 'left' }}>{dimension === 'entry_cycle' ? 'Cycle (New York)' : dim.label}</th>
                          <th style={{ ...thStyle, textAlign: 'right' }}>Avg R</th>
                          <th style={{ ...thStyle, textAlign: 'left' }} title="Where the true average probably lies">Range (90%)</th>
                          <th style={{ ...thStyle, textAlign: 'right' }}>Trades</th>
                          <th style={{ ...thStyle, textAlign: 'right' }}>Win rate</th>
                          <th style={{ ...thStyle, textAlign: 'right' }}>Total R</th>
                          <th style={{ ...thStyle, textAlign: 'right' }} title="Skipped setups: share that would have hit a 2R target first (missed) or a 2 × ATR stop first (avoided) within 5 sessions">Model skips · missed / avoided</th>
                          <th style={{ ...thStyle, textAlign: 'left' }}>Sample</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dim.rows.length === 0 && (
                          <tr style={{ borderTop: '1px solid var(--sa-border)' }}>
                            <td colSpan={8} style={{ padding: '10px', color: 'var(--sa-text-muted)' }}>
                              Under 10 trades per slice — slices start counting at 10.
                            </td>
                          </tr>
                        )}
                        {dim.rows.map((r) => (
                          <tr key={r.slice} style={{ borderTop: '1px solid var(--sa-border)', opacity: r.status === 'early' ? 0.6 : 1 }}>
                            <td style={{ padding: '10px', verticalAlign: 'middle' }}>
                              {r.status !== 'rule' && r.status !== 'note' ? (
                                <button type="button" onClick={() => showTrades(dimension, r.slice)} style={{ padding: 0, border: 'none', background: 'transparent', color: 'var(--sa-text)', fontWeight: 600, fontFamily: 'var(--sa-font-mono)', textDecoration: 'underline', textUnderlineOffset: '3px', cursor: 'pointer', fontSize: 'inherit', textAlign: 'left' }}>{r.slice}</button>
                              ) : (
                                <b style={{ fontFamily: 'var(--sa-font-mono)', fontWeight: 600 }}>{r.slice}</b>
                              )}{' '}
                              {r.sub && <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{r.sub}</span>}
                            </td>
                            {r.status === 'rule' || r.status === 'note' ? (
                              <td colSpan={7} style={{ padding: '10px', color: 'var(--sa-text-muted)' }}>{r.rule ?? r.note}</td>
                            ) : (
                              <>
                                <td style={{ padding: '10px', fontFamily: 'var(--sa-font-mono)', textAlign: 'right', color: r.avg_r != null && Number(r.avg_r) >= 0 ? 'var(--sa-positive)' : 'var(--sa-negative-text)' }}>{fmtR(r.avg_r)}</td>
                                <td style={{ padding: '10px' }}><RangeBar lo={r.lo} hi={r.hi} avg={r.avg_r} /></td>
                                <td style={{ padding: '10px', fontFamily: 'var(--sa-font-mono)', textAlign: 'right' }}>{r.trades}</td>
                                <td style={{ padding: '10px', fontFamily: 'var(--sa-font-mono)', textAlign: 'right' }}>{fmtPct0(r.win_rate_pct)}</td>
                                <td style={{ padding: '10px', fontFamily: 'var(--sa-font-mono)', textAlign: 'right', color: r.total_r != null && Number(r.total_r) >= 0 ? 'var(--sa-positive)' : 'var(--sa-negative-text)' }}>{fmtR(r.total_r)}</td>
                                <td style={{ padding: '10px', fontFamily: 'var(--sa-font-mono)', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                  {r.skips == null ? '—' : r.skips === 0 ? '0' : (
                                    <>
                                      {r.skips.toLocaleString()} ·{' '}
                                      <span style={{ color: 'var(--sa-warning-text)' }}>{fmtPct0(r.skips_missed_pct)}</span>
                                      {' / '}
                                      <span style={{ color: 'var(--sa-positive)' }}>{fmtPct0(r.skips_avoided_pct)}</span>
                                    </>
                                  )}
                                </td>
                                <td style={{ padding: '10px' }}>
                                  {r.status === 'finding' ? (
                                    <Chip variant="accent" wrap>finding · 30+ trades</Chip>
                                  ) : r.status === 'early' ? (
                                    <Chip variant="neutral" wrap>early signal · 10–29 trades</Chip>
                                  ) : (
                                    <Chip variant="neutral" wrap>counted</Chip>
                                  )}
                                </td>
                              </>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {dim.hidden > 0 && <div style={{ ...mutedNote }}>+ {dim.hidden.toLocaleString()} trades in slices under 10</div>}
                  <div style={{ ...mutedNote, lineHeight: 1.5 }}>
                    Each slice is compared with all other trades. 30 or more trades: a finding when the difference is at least twice the noise. 10–29: early signal, greyed. Under 10: hidden and counted. Times in New York; Amsterdam alongside.
                  </div>
                  {tradeSlice !== null && (
                    <div aria-label={`Trades in ${tradeSlice}`} style={{ borderTop: '1px solid var(--sa-border)', paddingTop: 'var(--sa-space-2)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                        <strong>Trades in {tradeSlice}</strong>
                        <button type="button" onClick={() => setTradeSlice(null)} style={{ fontSize: 'var(--sa-text-sm)' }}>Close</button>
                      </div>
                      {tradesRes.loading && (
                        <div className="sa-loading">
                          <SkeletonTable
                            columns={['Closed', 'Symbol', 'Qty', 'R', 'P&L', 'Exit', ''].map(
                              (label, i) => ({ label, align: i === 0 || i === 1 ? 'left' : 'right' }),
                            )}
                            rows={4}
                            track="0.9fr 0.7fr 0.5fr 0.5fr 0.7fr 0.9fr 0.6fr"
                          />
                        </div>
                      )}
                      {tradesRes.failure && <div role="alert">{tradesRes.failure.message}</div>}
                      {tradesRes.data && tradesRes.data.trades.length === 0 && !tradesRes.loading && (
                        <div style={{ color: 'var(--sa-text-muted)' }}>No trades in this slice.</div>
                      )}
                      {tradesRes.data && tradesRes.data.trades.length > 0 && (
                        <div style={{ overflowX: 'auto', minWidth: 0 }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--sa-text-sm)' }}>
                            <thead><tr style={{ color: 'var(--sa-text-muted)', textAlign: 'left' }}>
                              <th style={{ padding: '4px 8px' }}>Closed</th>
                              <th style={{ padding: '4px 8px' }}>Symbol</th>
                              <th style={{ padding: '4px 8px' }}>Qty</th>
                              <th style={{ padding: '4px 8px' }}>R</th>
                              <th style={{ padding: '4px 8px' }}>P&L</th>
                              <th style={{ padding: '4px 8px' }}>Exit</th>
                              <th style={{ padding: '4px 8px' }}></th>
                            </tr></thead>
                            <tbody>
                              {tradesRes.data.trades.map((t) => (
                                <tr key={t.id} style={{ borderTop: '1px solid var(--sa-border)' }}>
                                  <td style={{ padding: '4px 8px', fontFamily: 'var(--sa-font-mono)' }}>{t.closed_at ? displayDayTime(t.closed_at) : '—'}</td>
                                  <td style={{ padding: '4px 8px' }}>{t.symbol}</td>
                                  <td style={{ padding: '4px 8px', fontFamily: 'var(--sa-font-mono)' }}>{t.qty}</td>
                                  <td style={{ padding: '4px 8px', fontFamily: 'var(--sa-font-mono)' }}>{fmtR(t.r)}</td>
                                  <td style={{ padding: '4px 8px', fontFamily: 'var(--sa-font-mono)' }}>{fmtUsd(t.pnl)}</td>
                                  <td style={{ padding: '4px 8px' }}>{labelFor(EXIT_REASONS, t.exit_reason)}</td>
                                  <td style={{ padding: '4px 8px' }}>
                                    {strategy === 'live' && t.decision_id != null ? (
                                      <Link to={`/decisions/${t.decision_id}`}>decision</Link>
                                    ) : (
                                      t.decision_id != null ? <span style={{ color: 'var(--sa-text-muted)' }}>decision #{t.decision_id}</span> : null
                                    )}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </Card>

            {/* sectors now — the last cycle's sector table (item 068) */}
            {res.data?.sectors_now && (
              <Card label="Sectors now" refreshing={res.refreshing} style={{ flex: '1 1 420px' }}>
                <div style={{ overflowX: 'auto', minWidth: 0 }}>
                  <table style={{ width: '100%', minWidth: '300px', borderCollapse: 'collapse', fontSize: 'var(--sa-text-md)' }}>
                    <thead>
                      <tr style={{ color: 'var(--sa-text-muted)', textAlign: 'left' }}>
                        <th scope="col" style={{ ...thStyle, textAlign: 'left' }}>Rank</th>
                        <th scope="col" style={{ ...thStyle, textAlign: 'left' }}>Sector</th>
                        <th scope="col" style={{ ...thStyle, textAlign: 'right' }}>vs SPY · 20 × 1h bars</th>
                        <th scope="col" style={{ ...thStyle, textAlign: 'left' }}>ETF</th>
                      </tr>
                    </thead>
                    <tbody>
                      {res.data.sectors_now.sectors.map((e) => (
                        <tr key={e.sector} style={{ borderTop: '1px solid var(--sa-border)' }}>
                          <td style={{ padding: '10px', fontFamily: 'var(--sa-font-mono)' }}>{e.rank ?? '—'}</td>
                          <td style={{ padding: '10px' }}>{e.sector}</td>
                          <td style={{ padding: '10px', fontFamily: 'var(--sa-font-mono)', textAlign: 'right', color: e.rs_vs_spy_pct != null && e.rs_vs_spy_pct >= 0 ? 'var(--sa-positive)' : 'var(--sa-negative-text)' }}>
                            {e.rs_vs_spy_pct != null ? signedPct(e.rs_vs_spy_pct, 2) : '—'}
                          </td>
                          <td style={{ padding: '10px', fontFamily: 'var(--sa-font-mono)' }}>{e.etf ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div style={{ ...mutedNote, lineHeight: 1.5 }}>
                  At the {displayTime(res.data.sectors_now.at)} cycle, from each
                  sector ETF's bars with the same strength measure as the stocks.
                  {breadthNote(res.data.sectors_now.breadth)} Reference only:
                  nothing in trading uses it yet.
                </div>
              </Card>
            )}
          </div>

          {/* 5 · What do confidence, fills and nights cost? */}
          <Question id="q5" n={5} title="What do confidence, fills and nights cost?" answer={answers.costs} />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'flex-start' }}>
            {snap.calibration.buckets && (
              <Card
                label="Calibration"
                refreshing={res.refreshing}
                style={{ flex: '1 1 380px' }}
                right={
                  snap.calibration.weighted_gap_pts != null ? (
                    <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
                      weighted gap {snap.calibration.weighted_gap_pts} pts · target within 10
                    </span>
                  ) : (
                    <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
                      {sampleLine(snap.calibration, 'entries') ?? 'weighted gap needs more entries'}
                    </span>
                  )
                }
              >
                <div style={{ overflowX: 'auto', minWidth: 0 }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--sa-text-sm)' }}>
                    <thead><tr style={{ color: 'var(--sa-text-muted)', textAlign: 'left' }}>
                      <th style={{ padding: '6px 8px' }}>Confidence</th><th style={{ padding: '6px 8px' }}>Stated</th><th style={{ padding: '6px 8px' }}>Reached target first</th><th style={{ padding: '6px 8px' }}>n</th>
                    </tr></thead>
                    <tbody>
                      {snap.calibration.buckets.map((b) => (
                        <tr key={b.label} style={{ borderTop: '1px solid var(--sa-border)', opacity: b.status === 'early' ? 0.6 : 1 }}>
                          <td style={{ padding: '6px 8px' }}>{b.label}</td>
                          <td style={{ padding: '6px 8px', fontFamily: 'var(--sa-font-mono)' }}>{b.stated ?? '—'}</td>
                          <td style={{ padding: '6px 8px', fontFamily: 'var(--sa-font-mono)' }}>{b.actual ?? '—'}</td>
                          <td style={{ padding: '6px 8px', fontFamily: 'var(--sa-font-mono)' }}>{b.n}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {snap.calibration.hidden ? <div style={{ ...mutedNote }}>+ {snap.calibration.hidden} entries in buckets under 10</div> : null}
                {sampleLine(snap.calibration, 'entries') && (
                  <div style={{ ...mutedNote }}>The weighted gap counts once {snap.calibration.needs ?? 50} entries carry a confidence; {snap.calibration.have ?? 0} so far.</div>
                )}
              </Card>
            )}
            <Card
              label="Execution"
              refreshing={res.refreshing}
              style={{ flex: '1 1 380px' }}
              right={strategy !== 'live' ? <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>virtual fills{strategyKind === 'rules_only' ? ' · rules only' : ''}</span> : undefined}
            >
              <Fact label="Entry fill rate" note={snap.execution.worst_cycle ? `worst ${snap.execution.worst_cycle.cycle}: ${snap.execution.worst_cycle.filled} of ${snap.execution.worst_cycle.placed}` : undefined}>
                {snap.execution.fill_rate != null ? `${fmtPct0(snap.execution.fill_rate)} · ${snap.execution.filled} of ${snap.execution.placed}` : '—'}
              </Fact>
              <Fact label="Entry slippage" note="fill vs market price at the decision">
                {snap.execution.entry_slippage_bps != null ? `${snap.execution.entry_slippage_bps} bps` : '—'}
              </Fact>
              <Fact label="Exit slippage" note={snap.execution.exit_slippage_splits.length > 0 ? snap.execution.exit_slippage_splits.map((s) => `${labelFor(REASON_CODES, s.reason)} ${s.bps ?? '—'}`).join(' · ') : undefined}>
                {snap.execution.exit_slippage_bps != null ? `${snap.execution.exit_slippage_bps} bps` : '—'}
              </Fact>
              <Fact label="Stops filled through the stop" note="extra R beyond −1R" color="var(--sa-negative-text)">
                {snap.execution.gaps_count} · {snap.execution.gaps_extra_r != null ? `${fmtR(snap.execution.gaps_extra_r)} extra` : 'no extra R'}
              </Fact>
              <Fact label="Agent exits on the entry day" note="sold the same session when the reason no longer held">
                {snap.execution.same_day_exits}{snap.execution.same_day_avg_r != null && ` · avg ${fmtR(snap.execution.same_day_avg_r)}`}
              </Fact>
              <Fact label="Earnings exits" note={snap.execution.earnings_median_move_pct != null ? `median ${fmtPct0(snap.execution.earnings_median_move_pct)} after` : undefined}>
                {snap.execution.earnings_exits}{snap.execution.earnings_avg_r != null && ` · avg ${fmtR(snap.execution.earnings_avg_r)}`}
              </Fact>
              <Fact label="Dividends">
                {fmtUsd(snap.execution.dividends_usd)}{snap.execution.dividend_positions > 0 && ` across ${snap.execution.dividend_positions} positions`}
              </Fact>
            </Card>
            <Card label="Overnight and in session" refreshing={res.refreshing} style={{ flex: '1 1 380px' }} right={<span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>held positions · adjusted</span>}>
              {(() => {
                const inS = snap.overnight.in_session_usd != null ? Math.abs(Number(snap.overnight.in_session_usd)) : 0;
                const on = snap.overnight.overnight_usd != null ? Math.abs(Number(snap.overnight.overnight_usd)) : 0;
                const max = Math.max(inS, on, 1);
                return (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '110px minmax(0, 1fr) 80px', gap: 10, alignItems: 'center', fontSize: 'var(--sa-text-md)' }}>
                      <span style={{ color: 'var(--sa-text-2)' }}>In session</span>
                      <div style={{ height: 10, background: 'var(--sa-raised)', borderRadius: 5 }}>
                        <div style={{ width: `${(inS / max) * 100}%`, height: 10, background: 'var(--sa-positive)', borderRadius: 5 }} />
                      </div>
                      <span style={{ fontFamily: 'var(--sa-font-mono)', textAlign: 'right', color: 'var(--sa-positive)' }}>{fmtUsd(snap.overnight.in_session_usd)}</span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '110px minmax(0, 1fr) 80px', gap: 10, alignItems: 'center', fontSize: 'var(--sa-text-md)' }}>
                      <span style={{ color: 'var(--sa-text-2)' }}>Overnight</span>
                      <div style={{ height: 10, background: 'var(--sa-raised)', borderRadius: 5 }}>
                        <div style={{ width: `${(on / max) * 100}%`, height: 10, background: 'var(--sa-negative)', borderRadius: 5 }} />
                      </div>
                      <span style={{ fontFamily: 'var(--sa-font-mono)', textAlign: 'right', color: 'var(--sa-negative-text)' }}>{fmtUsd(snap.overnight.overnight_usd)}</span>
                    </div>
                    <div style={{ ...mutedNote, lineHeight: 1.5 }}>
                      Positions were held over {snap.overnight.nights.toLocaleString()} nights.
                      {snap.overnight.gapped_pct != null && ` ${fmtPct0(snap.overnight.gapped_pct)} of those nights gapped against the position`}
                      {snap.overnight.largest_gap && `; the largest gap was ${fmtR(snap.overnight.largest_gap.r)} (${snap.overnight.largest_gap.symbol}, ${sessionDayShort(snap.overnight.largest_gap.date)})`}
                      . Overnight risk is what a stop at Alpaca can't fully cover.
                    </div>
                  </div>
                );
              })()}
            </Card>
          </div>
        </>
      )}

      {res.loading && !snap && (
        <div className="sa-loading" aria-hidden="true">
          <SkeletonTiles count={4} />
          <Card ariaLabel="Findings">
            <SkeletonList rows={3} />
          </Card>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'flex-start' }}>
            {[0, 1].map((i) => (
              <Card key={i} ariaLabel="Card">
                <SkeletonText lines={4} />
              </Card>
            ))}
          </div>
          <Card ariaLabel="Slices">
            <SkeletonTable
              columns={['Slice', 'Avg R', 'Range', 'Trades', 'Win rate', 'Total R', 'Skips', 'Sample'].map(
                (label, i) => ({ label, align: i === 0 ? 'left' : 'right' }),
              )}
              rows={6}
              track="1.4fr 0.6fr 1fr 0.6fr 0.7fr 0.7fr 0.9fr 0.9fr"
            />
          </Card>
        </div>
      )}
    </div>
  );
}
