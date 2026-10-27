import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { get } from '../api/client';
import { useApiResource } from '../api/useResource';
import { Card } from '../components/Card';
import { SegmentedControl } from '../components/SegmentedControl';
import { StatCard } from '../components/StatCard';

/** One insights snapshot as the API serves it (docs/SPEC.md, Insights). */

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
}

interface Snapshot {
  session_date: string;
  computed_at: string;
  trades: number;
  headline: {
    expectancy: { avg_r: string | null; lo: string | null; hi: string | null; n: number };
    edge_over_spy: { pts: string | null; return_pct: string | null; spy_return_pct: string | null; exposure: string | null };
    beta: { beta: string | null; correlation: string | null; sessions: number };
    overnight: { in_session_usd: string | null; overnight_usd: string | null };
    gaps: { count: number; extra_r: string | null };
    model_cost: { total_usd: string | null; per_trade_usd: string | null; pct_gross_profit: string | null };
  };
  funnel: { steps: { key: string; count: number; losses: { label: string; count: number }[] }[] };
  findings: Finding[];
  skipped: {
    total: number; scored?: number; missed?: number; avoided?: number; neither?: number;
    missed_pct?: string | null; avoided_pct?: string | null; neither_pct?: string | null;
    baseline_r?: string | null; not_filled?: number; na?: string;
  };
  slices: Record<string, { label: string; rows: SliceRow[]; hidden: number }>;
  calibration: {
    buckets?: { label: string; stated: string | null; actual: string | null; n: number; status: string }[];
    hidden?: number; entries?: number; weighted_gap_pts?: string | null; na?: string;
  };
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

interface InsightsResponse {
  strategies: { id: string; name: string; kind: string }[];
  period: string;
  empty: boolean;
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
  rules: 'Passed the rules',
  called: 'Reached the model',
  proposed: 'Model proposed an entry',
  code_checks: 'Passed code checks',
  risk_gates: 'Passed risk gates',
  filled: 'Order filled',
  closed: 'Closed trades',
};

/** "Test in a shadow" applies only to the dimensions that map to a
 * strategy override — today that's just the entry cycle. */
const SHADOWABLE: Record<string, (slice: string) => Record<string, unknown>> = {
  entry_cycle: (slice) => ({ entry_cycles_excluded: slice }),
};

function fmtR(v: string | null | undefined): string {
  if (v == null) return '—';
  const n = Number(v);
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}R`;
}
function fmtUsd(v: string | null | undefined): string {
  if (v == null) return '—';
  const n = Number(v);
  return `${n < 0 ? '−' : ''}$${Math.abs(n).toFixed(2)}`;
}
function fmtPct(v: string | null | undefined): string {
  return v == null ? '—' : `${Number(v).toFixed(1)}%`;
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

const DEFS: Record<string, string> = {
  expectancy: 'Average adjusted R per closed trade. The band is the 90% range: ± 1.64 standard errors on the mean.',
  edge: 'Run return minus SPY’s return over the same sessions, at the strategy’s average equity exposure.',
  beta: 'Slope and correlation of daily equity returns against SPY over 20 sessions.',
  overnight: 'P&L of held positions from the previous close to the open, versus from the open to the close or exit.',
  gaps: 'Stop exits that filled more than 0.1R below the stop, and the extra R lost beyond −1R.',
  cost: 'Decision-model cost in the period, per closed trade and as a share of gross profit.',
};

export default function Insights() {
  const [strategy, setStrategy] = useState('live');
  const [period, setPeriod] = useState('run');
  const [dimension, setDimension] = useState('entry_cycle');
  const [tradeSlice, setTradeSlice] = useState<string | null>(null);
  const navigate = useNavigate();
  const res = useApiResource<InsightsResponse>(
    () => get<InsightsResponse>(`/api/insights?strategy=${strategy}&period=${period}`),
    [strategy, period],
  );
  const tradesRes = useApiResource<InsightTradesResponse>(
    () =>
      get<InsightTradesResponse>(
        `/api/insights/trades?strategy=${strategy}&period=${period}&dimension=${dimension}&slice=${encodeURIComponent(tradeSlice ?? '')}`,
      ),
    [strategy, period, dimension, tradeSlice],
    tradeSlice !== null,
  );
  const showTrades = (dim: string, slice: string) => {
    setDimension(dim);
    setTradeSlice(slice);
  };
  const strategyKind = res.data?.strategies.find((s) => s.id === strategy)?.kind;
  const snap = res.data?.snapshot ?? null;
  const dim = snap?.slices?.[dimension];
  const beforeData = snap != null && snap.trades < 10;

  const tile = (label: string, def: string) => (
    <span title={def} style={{ borderBottom: '1px dotted var(--sa-text-muted)', cursor: 'help' }}>{label}</span>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-4)', padding: 'var(--sa-space-4)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sa-space-4)', flexWrap: 'wrap' }}>
        <h1 style={{ margin: 0, fontSize: 'var(--sa-text-xl)' }}>Insights</h1>
        <SegmentedControl
          label="Strategy"
          options={(res.data?.strategies ?? [{ id: 'live', name: 'Live' }]).map((s) => s.name)}
          value={(res.data?.strategies ?? []).find((s) => s.id === strategy)?.name ?? 'Live'}
          onChange={(name) => {
            const s = (res.data?.strategies ?? []).find((x) => x.name === name);
            if (s) setStrategy(s.id);
          }}
        />
        <SegmentedControl
          label="Period"
          options={PERIODS.map(([, label]) => label)}
          value={PERIODS.find(([k]) => k === period)?.[1] ?? 'This run'}
          onChange={(label) => setPeriod(PERIODS.find(([, l]) => l === label)?.[0] ?? 'run')}
        />
        {snap && <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>{snap.trades} closed trades · snapshot {snap.session_date}</span>}
      </div>

      {res.failure && <Card><div role="alert">{res.failure.message}</div></Card>}
      {res.data?.empty && !res.loading && <Card><div>No insights snapshot yet — the daily job writes one after each session close.</div></Card>}

      {snap && (
        <>
          {/* headline tiles */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)' }}>
            <StatCard
              label={tile('Expectancy', DEFS.expectancy)}
              value={fmtR(snap.headline.expectancy.avg_r)}
              sub={snap.headline.expectancy.lo != null ? `90% range ${fmtR(snap.headline.expectancy.lo)} to ${fmtR(snap.headline.expectancy.hi)} · n=${snap.headline.expectancy.n}` : 'n=0'}
            />
            <StatCard
              label={tile('Edge over SPY', DEFS.edge)}
              value={snap.headline.edge_over_spy.pts != null ? `${Number(snap.headline.edge_over_spy.pts) >= 0 ? '+' : ''}${snap.headline.edge_over_spy.pts} pts` : '—'}
              sub={`run ${fmtPct(snap.headline.edge_over_spy.return_pct)} vs SPY ${fmtPct(snap.headline.edge_over_spy.spy_return_pct)} × ${snap.headline.edge_over_spy.exposure ?? '—'} exposure`}
            />
            <StatCard
              label={tile('Beta / correlation', DEFS.beta)}
              value={snap.headline.beta.beta != null ? `β ${snap.headline.beta.beta} · r ${snap.headline.beta.correlation}` : '—'}
              sub={`${snap.headline.beta.sessions} sessions`}
            />
            <StatCard
              label={tile('Overnight vs in-session', DEFS.overnight)}
              value={`${fmtUsd(snap.headline.overnight.overnight_usd)} / ${fmtUsd(snap.headline.overnight.in_session_usd)}`}
              sub="overnight · in session"
            />
            <StatCard
              label={tile('Gaps through stop', DEFS.gaps)}
              value={String(snap.headline.gaps.count)}
              sub={snap.headline.gaps.extra_r != null ? `extra ${fmtR(snap.headline.gaps.extra_r)} beyond −1R` : 'no extra R'}
            />
            <StatCard
              label={tile('Model cost per trade', DEFS.cost)}
              value={fmtUsd(snap.headline.model_cost.per_trade_usd)}
              sub={snap.headline.model_cost.pct_gross_profit != null ? `${snap.headline.model_cost.pct_gross_profit}% of gross profit · ${fmtUsd(snap.headline.model_cost.total_usd)} total` : fmtUsd(snap.headline.model_cost.total_usd)}
            />
          </div>

          {beforeData && (
            <Card ariaLabel="Before data">
              <div style={{ color: 'var(--sa-text-muted)' }}>
                Before data — under 10 closed trades. Slices and findings appear once the smallest sample counts; the funnel, skips and execution below already fill in.
              </div>
            </Card>
          )}

          {/* findings */}
          <Card label="Findings">
            {snap.findings.length === 0 && <div style={{ color: 'var(--sa-text-muted)' }}>No findings in this period. Nothing differed by more than two standard errors on a big enough sample.</div>}
            {snap.findings.map((f) => (
              <div key={`${f.dimension}|${f.slice}`} style={{ border: '1px solid var(--sa-border)', borderLeft: `3px solid ${f.status === 'finding' ? 'var(--sa-accent)' : 'var(--sa-border)'}`, borderRadius: 'var(--sa-radius-md)', padding: 'var(--sa-space-3)', opacity: f.status === 'early' ? 0.65 : 1, display: 'flex', justifyContent: 'space-between', gap: 'var(--sa-space-3)', flexWrap: 'wrap' }}>
                <div>
                  <div style={{ fontWeight: 600 }}>{f.dimension_label}: {f.slice} {f.new && <span style={{ color: 'var(--sa-accent)' }}>· new</span>}</div>
                  <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
                    {f.trades} trades at {fmtR(f.avg_r)} vs {fmtR(f.rest_avg_r)} for the other {f.rest_trades} — {f.sigma}× the noise{f.status === 'early' ? ' · early signal, sample still small' : ''}{f.held_days ? ` · held for ${f.held_days} day${f.held_days === 1 ? '' : 's'}` : ''}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 'var(--sa-space-2)', alignItems: 'center' }}>
                  {SHADOWABLE[f.dimension] && f.status === 'finding' && (
                    <button type="button" onClick={() => navigate(`/strategies?new_shadow=1&exclude_cycle=${encodeURIComponent(f.slice)}`)}>Test in a shadow</button>
                  )}
                  <button type="button" onClick={() => showTrades(f.dimension, f.slice)}>Show the trades</button>
                </div>
              </div>
            ))}
          </Card>

          {/* funnel + skipped setups */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 'var(--sa-space-3)' }}>
            <Card label="Where trades come from">
              {snap.funnel.steps.map((s, i) => {
                const prev = i > 0 ? snap.funnel.steps[i - 1].count : s.count;
                return (
                  <div key={s.key} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span>{FUNNEL_LABELS[s.key] ?? s.key}</span>
                      <span style={{ fontFamily: 'var(--sa-font-mono)' }}>{s.count}</span>
                    </div>
                    <div style={{ height: 4, background: 'var(--sa-raised)', borderRadius: 2 }}>
                      <div style={{ width: `${snap.funnel.steps[0].count ? (s.count / snap.funnel.steps[0].count) * 100 : 0}%`, height: '100%', background: 'var(--sa-accent)', borderRadius: 2 }} />
                    </div>
                    {s.losses.filter((l) => l.count > 0).map((l) => (
                      <span key={l.label} style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>−{l.count} {l.label}</span>
                    ))}
                    {prev === s.count && i > 0 ? null : null}
                  </div>
                );
              })}
            </Card>
            <Card label="Skipped setups">
              {snap.skipped.na ? (
                <div style={{ color: 'var(--sa-text-muted)' }}>{snap.skipped.na}</div>
              ) : (
                <>
                  <div style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-stat)' }}>{snap.skipped.total} skips</div>
                  <div>Would have reached the target first — missed <strong>{fmtPct(snap.skipped.missed_pct)}</strong></div>
                  <div>Would have hit the stop first — avoided <strong>{fmtPct(snap.skipped.avoided_pct)}</strong></div>
                  <div>Neither within the horizon: <strong>{fmtPct(snap.skipped.neither_pct)}</strong></div>
                  <div style={{ color: 'var(--sa-text-muted)' }}>Not filled — the limit was never touched: <strong>{snap.skipped.not_filled ?? 0}</strong></div>
                  <div style={{ color: 'var(--sa-text-muted)' }}>Taking every skip with the rules-only geometry would have averaged {fmtR(snap.skipped.baseline_r ?? null)}.</div>
                </>
              )}
            </Card>
          </div>

          {/* slices */}
          <Card label="Slices" right={
            <select aria-label="Dimension" value={dimension} onChange={(e) => setDimension(e.target.value)} style={{ background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', color: 'var(--sa-text)', borderRadius: 'var(--sa-radius-md)', height: 32, padding: '0 8px' }}>
              {Object.entries(snap.slices).map(([key, d]) => (
                <option key={key} value={key}>{d.label}</option>
              ))}
            </select>
          }>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
              {Object.entries(snap.slices).map(([key, d]) => (
                <button key={key} type="button" aria-pressed={key === dimension} onClick={() => setDimension(key)} style={{ padding: '4px 10px', borderRadius: 'var(--sa-radius-sm)', border: '1px solid var(--sa-border)', background: key === dimension ? 'var(--sa-raised)' : 'transparent', color: 'var(--sa-text)', cursor: 'pointer', fontSize: 'var(--sa-text-sm)' }}>
                  {d.label}
                </button>
              ))}
            </div>
            {dim && (
              <>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--sa-text-sm)' }}>
                  <thead>
                    <tr style={{ color: 'var(--sa-text-muted)', textAlign: 'left' }}>
                      <th style={{ padding: '6px 8px' }}>{dim.label}</th>
                      <th style={{ padding: '6px 8px' }}>Trades</th>
                      <th style={{ padding: '6px 8px' }}>Win rate</th>
                      <th style={{ padding: '6px 8px' }}>Avg R</th>
                      <th style={{ padding: '6px 8px' }}>90% range</th>
                      <th style={{ padding: '6px 8px' }}>Total R</th>
                      <th style={{ padding: '6px 8px' }}>Skips</th>
                      <th style={{ padding: '6px 8px' }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dim.rows.map((r) => (
                      <tr key={r.slice} style={{ borderTop: '1px solid var(--sa-border)', opacity: r.status === 'early' ? 0.6 : 1 }}>
                        <td style={{ padding: '6px 8px' }}>
                          {r.status !== 'rule' ? (
                            <button type="button" onClick={() => showTrades(dimension, r.slice)} style={{ color: 'var(--sa-text)', textDecoration: 'underline', textUnderlineOffset: '3px', cursor: 'pointer' }}>{r.slice}</button>
                          ) : (
                            r.slice
                          )}
                          {r.sub && <span style={{ display: 'block', color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>{r.sub}</span>}
                        </td>
                        {r.status === 'rule' ? (
                          <td colSpan={7} style={{ padding: '6px 8px', color: 'var(--sa-text-muted)' }}>{r.rule}</td>
                        ) : (
                          <>
                            <td style={{ padding: '6px 8px', fontFamily: 'var(--sa-font-mono)' }}>{r.trades}</td>
                            <td style={{ padding: '6px 8px', fontFamily: 'var(--sa-font-mono)' }}>{fmtPct(r.win_rate_pct)}</td>
                            <td style={{ padding: '6px 8px', fontFamily: 'var(--sa-font-mono)' }}>{fmtR(r.avg_r)}</td>
                            <td style={{ padding: '6px 8px' }}><RangeBar lo={r.lo} hi={r.hi} avg={r.avg_r} /></td>
                            <td style={{ padding: '6px 8px', fontFamily: 'var(--sa-font-mono)' }}>{fmtR(r.total_r)}</td>
                            <td style={{ padding: '6px 8px', fontFamily: 'var(--sa-font-mono)' }}>{r.skips ? `${r.skips} · ${fmtPct(r.skips_missed_pct)} missed` : '—'}</td>
                            <td style={{ padding: '6px 8px' }}>{r.status === 'finding' ? <strong>finding</strong> : r.status === 'early' ? <span style={{ color: 'var(--sa-text-muted)' }}>early</span> : <span style={{ color: 'var(--sa-text-muted)' }}>counted</span>}</td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
                {dim.hidden > 0 && <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>+ {dim.hidden} trades in slices under 10</div>}
                {tradeSlice !== null && (
                  <div aria-label={`Trades in ${tradeSlice}`} style={{ borderTop: '1px solid var(--sa-border)', paddingTop: 'var(--sa-space-2)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                      <strong>Trades in {tradeSlice}</strong>
                      <button type="button" onClick={() => setTradeSlice(null)} style={{ fontSize: 'var(--sa-text-sm)' }}>Close</button>
                    </div>
                    {tradesRes.loading && <div style={{ color: 'var(--sa-text-muted)' }}>Loading…</div>}
                    {tradesRes.failure && <div role="alert">{tradesRes.failure.message}</div>}
                    {tradesRes.data && tradesRes.data.trades.length === 0 && !tradesRes.loading && (
                      <div style={{ color: 'var(--sa-text-muted)' }}>No trades in this slice.</div>
                    )}
                    {tradesRes.data && tradesRes.data.trades.length > 0 && (
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
                              <td style={{ padding: '4px 8px', fontFamily: 'var(--sa-font-mono)' }}>{t.closed_at ? t.closed_at.slice(0, 10) : '—'}</td>
                              <td style={{ padding: '4px 8px' }}>{t.symbol}</td>
                              <td style={{ padding: '4px 8px', fontFamily: 'var(--sa-font-mono)' }}>{t.qty}</td>
                              <td style={{ padding: '4px 8px', fontFamily: 'var(--sa-font-mono)' }}>{fmtR(t.r)}</td>
                              <td style={{ padding: '4px 8px', fontFamily: 'var(--sa-font-mono)' }}>{fmtUsd(t.pnl)}</td>
                              <td style={{ padding: '4px 8px' }}>{t.exit_reason ?? '—'}</td>
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
                    )}
                  </div>
                )}
              </>
            )}
          </Card>

          {/* calibration */}
          {snap.calibration.buckets && (
            <Card label="Calibration" right={snap.calibration.weighted_gap_pts != null ? <span style={{ color: 'var(--sa-text-muted)' }}>weighted gap {snap.calibration.weighted_gap_pts} pts · target before stop</span> : undefined}>
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
              {snap.calibration.hidden ? <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>+ {snap.calibration.hidden} entries in buckets under 10</div> : null}
            </Card>
          )}

          {/* execution + overnight */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 'var(--sa-space-3)' }}>
            <Card label="Execution" right={strategy !== 'live' ? <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>virtual fills{strategyKind === 'rules_only' ? ' · rules only' : ''}</span> : undefined}>
              <div>Entry fill rate: <strong>{fmtPct(snap.execution.fill_rate)}</strong> ({snap.execution.filled} of {snap.execution.placed}){snap.execution.worst_cycle && ` · worst ${snap.execution.worst_cycle.cycle}`}</div>
              <div>Entry slippage: <strong>{snap.execution.entry_slippage_bps ?? '—'} bps</strong></div>
              <div>Exit slippage: <strong>{snap.execution.exit_slippage_bps ?? '—'} bps</strong>{snap.execution.exit_slippage_splits.length > 0 && ` (${snap.execution.exit_slippage_splits.map((s) => `${s.reason} ${s.bps ?? '—'}`).join(' · ')})`}</div>
              <div>Gaps through stop: <strong>{snap.execution.gaps_count}</strong>{snap.execution.gaps_extra_r != null && ` · ${fmtR(snap.execution.gaps_extra_r)} extra`}</div>
              <div>Agent exits on the entry day: <strong>{snap.execution.same_day_exits}</strong>{snap.execution.same_day_avg_r != null && ` · avg ${fmtR(snap.execution.same_day_avg_r)}`}</div>
              <div>Earnings exits: <strong>{snap.execution.earnings_exits}</strong>{snap.execution.earnings_avg_r != null && ` · avg ${fmtR(snap.execution.earnings_avg_r)}`}{snap.execution.earnings_median_move_pct != null && ` · median ${fmtPct(snap.execution.earnings_median_move_pct)} after`}</div>
              <div>Dividends: <strong>{fmtUsd(snap.execution.dividends_usd)}</strong>{snap.execution.dividend_positions > 0 && ` across ${snap.execution.dividend_positions} positions`}</div>
            </Card>
            <Card label="Overnight">
              <div>In session: <strong>{fmtUsd(snap.overnight.in_session_usd)}</strong> · overnight: <strong>{fmtUsd(snap.overnight.overnight_usd)}</strong></div>
              <div>Nights held: <strong>{snap.overnight.nights}</strong>{snap.overnight.gapped_pct != null && ` · ${fmtPct(snap.overnight.gapped_pct)} gapped against`}</div>
              {snap.overnight.largest_gap && <div>Largest gap: <strong>{fmtR(snap.overnight.largest_gap.r)}</strong> {snap.overnight.largest_gap.symbol} on {snap.overnight.largest_gap.date}</div>}
            </Card>
          </div>
        </>
      )}
      {res.loading && !snap && <Card busy><div>Loading…</div></Card>}
    </div>
  );
}
