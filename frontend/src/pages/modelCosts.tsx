import { useSearchParams } from 'react-router-dom';

import { get } from '../api/client';
import { useApiResource } from '../api/useResource';
import { Card } from '../components/Card';
import { SegmentedControl } from '../components/SegmentedControl';
import { ErrorBanner } from '../components/cardState';
import { SkeletonTable, SkeletonTiles } from '../components/skeletons';
import { SessionChart } from './modelCostsChart';
import type { SessionBucket, SessionSummary, SessionUnit } from './modelCostsFormat';

/** Insights → "What do the models cost?" (item 109, docs/SPEC.md →
 * Model costs; design/screens/Insights.dc.html#q6). Whole-app spend,
 * computed on request — it is not part of the strategy snapshot, so it
 * fetches its own endpoint and keeps its period in `?costs=`. */

interface Cap {
  kind: 'monthly' | 'per_cycle';
  used?: string;
  limit?: string;
  pct?: number | null;
  max_calls_per_cycle?: number;
}

interface PurposeRow {
  key: string;
  label: string;
  models: string[];
  note: string | null;
  facts: Record<string, unknown>;
  calls: number;
  per_session: number | null;
  tokens_in: number;
  tokens_cached_in: number;
  cached_share: number | null;
  tokens_cache_write_in: number;
  tokens_out: number;
  cost: string | null;
  cost_unknown: boolean;
  cache_saved: string | null;
  cap: Cap | null;
}

interface ModelRow {
  id: string;
  provider: string | null;
  input: string | null;
  cached_input: string | null;
  cache_write: string | null;
  output: string | null;
  purposes: string[];
  calls: number;
  tokens_in: number;
  tokens_cached_in: number;
  tokens_cache_write_in: number;
  tokens_out: number;
  cost: string | null;
  cost_unknown: boolean;
  cache_saved: string | null;
  per_call: string | null;
}

interface ModelCostsResponse {
  period: { name: string; label: string; from: string | null; to: string; sessions: number };
  purposes: PurposeRow[];
  models: ModelRow[];
  per_session: SessionBucket[];
  per_session_unit: SessionUnit;
  per_session_summary: SessionSummary | null;
  answer: string;
}

const COST_PERIODS: [string, string][] = [
  ['this_month', 'This month'],
  ['last_month', 'Last month'],
  ['3_months', '3 months'],
  ['all', 'All time'],
];

const PURPOSE_LABELS: Record<string, string> = {
  live: 'Live decisions',
  shadow: 'Shadows',
  weekly: 'Weekly review',
  briefing: 'Daily briefing',
  verdict: 'Verdicts',
  ask: 'Ask',
  backtest: 'Backtests',
  total: 'Total',
};

/** The inspector source a purpose's calls live under (docs/SPEC.md,
 * LLM call history — sources). Backtests open the Backtest screen. */
function inspectorHref(key: string, from: string | null, to: string): string | null {
  const source =
    key === 'live'
      ? 'live'
      : key === 'shadow'
        ? 'shadow'
        : key === 'ask'
          ? 'ask'
          : key === 'backtest'
            ? 'backtest'
            : 'review';
  if (source === 'backtest') return '/backtest';
  const params = new URLSearchParams({ source });
  if (from) {
    params.set('period', 'custom');
    params.set('from', from);
    params.set('to', to);
  } else {
    params.set('period', 'all');
  }
  return `/inspector?${params.toString()}`;
}

function fmtUsd(v: string | null | undefined): string {
  if (v == null) return '—';
  const n = Number(v);
  if (Math.abs(n) > 0 && Math.abs(n) < 0.005) return '<$0.01';
  return `$${n.toFixed(2)}`;
}

function fmtPerCall(v: string | null | undefined): string {
  if (v == null) return '—';
  const n = Number(v);
  if (n > 0 && n < 0.0005) return '<$0.001';
  return `$${n.toFixed(4)}`;
}

/** 33.14M · 620k · 9.0k — compact token counts like the design. */
function fmtTokens(n: number): string {
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return `${m >= 10 ? m.toFixed(1) : m.toFixed(2).replace(/0$/, '')}M`;
  }
  if (n >= 1_000) {
    const k = n / 1_000;
    return `${k >= 10 ? Math.round(k) : k.toFixed(1)}k`;
  }
  return n.toLocaleString();
}

function fmtInt(n: number): string {
  return n.toLocaleString();
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
  padding: '10px',
  borderBottom: '1px solid var(--sa-border)',
  fontSize: 'var(--sa-text-md)',
  verticalAlign: 'top',
};
const numTd: React.CSSProperties = {
  ...tdStyle,
  fontFamily: 'var(--sa-font-mono)',
  textAlign: 'right',
  whiteSpace: 'nowrap',
};
const subNote: React.CSSProperties = {
  fontSize: 'var(--sa-text-xs)',
  color: 'var(--sa-text-muted)',
  marginTop: 4,
  lineHeight: 1.45,
};

function CapCell({ row }: { row: PurposeRow }) {
  const cap = row.cap;
  if (cap?.kind === 'per_cycle') {
    return (
      <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)', lineHeight: 1.45 }}>
        no monthly cap · at most {cap.max_calls_per_cycle} calls a cycle
      </span>
    );
  }
  if (cap?.kind === 'monthly' && cap.used != null && cap.limit != null) {
    const pct = cap.pct ?? 0;
    const barColor = pct >= 80 ? 'var(--sa-warning-text)' : 'var(--sa-info)';
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 120 }}>
        <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', whiteSpace: 'nowrap' }}>
          {fmtUsd(cap.used)} of ${Number(cap.limit).toFixed(0)} · {Math.round(pct)}%
        </span>
        <div style={{ height: 6, background: 'var(--sa-subtle)', borderRadius: 3 }}>
          <div className="sa-bar-fill" style={{ width: `${Math.min(100, pct)}%`, height: 6, background: barColor, borderRadius: 3 }} />
        </div>
        {(row.key === 'weekly' || row.key === 'ask' || row.key === 'shadow' || row.key === 'backtest') && (
          <div style={{ fontSize: 'var(--sa-text-xs)', color: 'var(--sa-text-muted)', marginTop: 4 }}>
            {row.key === 'weekly' ? 'reviews, briefings and verdicts together' : 'resets on the 1st'}
          </div>
        )}
      </div>
    );
  }
  if (row.key === 'briefing' || row.key === 'verdict') {
    return <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>in the review cap</span>;
  }
  return <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>—</span>;
}

function purposeNote(row: PurposeRow): string | null {
  const facts = row.facts ?? {};
  const parts: string[] = [];
  if (row.key === 'ask' && typeof facts.questions === 'number' && facts.questions > 0) {
    parts.push(
      `${facts.questions} question${facts.questions === 1 ? '' : 's'}` +
        (facts.cost_per_question ? ` · $${facts.cost_per_question} a question` : '') +
        (facts.calls_per_question ? ` · ${facts.calls_per_question} calls each` : ''),
    );
  }
  if (row.note) parts.push(row.note);
  return parts.length ? parts.join(' · ') : null;
}

function PricesLine({ row }: { row: ModelRow }) {
  const price = (v: string | null) => (v == null ? '—' : Number(v).toFixed(2).replace(/0+$/, '').replace(/\.$/, ''));
  const parts = [`in ${price(row.input)}`, `cached ${price(row.cached_input)}`];
  if (row.cache_write != null) parts.push(`write ${price(row.cache_write)}`);
  parts.push(`out ${price(row.output)}`);
  return (
    <div
      title="USD per million tokens, from the models block in config"
      style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-xs)', color: 'var(--sa-text-muted)', marginTop: 4 }}
    >
      {parts.join(' · ')} per 1M
    </div>
  );
}

export function ModelCostsSection() {
  const [searchParams, setSearchParams] = useSearchParams();
  const costs = searchParams.get('costs') ?? 'this_month';
  const period = COST_PERIODS.some(([k]) => k === costs) ? costs : 'this_month';
  const res = useApiResource<ModelCostsResponse>(
    (signal) => get<ModelCostsResponse>(`/api/insights/model-costs?period=${period}`, signal),
    [period],
  );
  const data = res.data;

  const setPeriod = (label: string) => {
    const key = COST_PERIODS.find(([, l]) => l === label)?.[0] ?? 'this_month';
    const next = new URLSearchParams(searchParams);
    next.set('costs', key);
    setSearchParams(next, { replace: true });
  };

  const costPeriodLabel = COST_PERIODS.find(([k]) => k === period)?.[1] ?? 'This month';

  const periodMeta = data
    ? `${data.period.from ? `${data.period.from.slice(8)} ` : ''}${data.period.from ? monthName(data.period.from) + ' ' : ''}– ${data.period.to.slice(8)} ${monthName(data.period.to)} · ${data.period.sessions} session${data.period.sessions === 1 ? '' : 's'} · read from every stored call, as of ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    : '';

  return (
    <div id="q6" style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 12, scrollMarginTop: 16 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', justifyContent: 'space-between', gap: '6px 12px' }}>
        <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>
          <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)', marginRight: 8 }}>6</span>
          What do the models cost?
        </h2>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>whole app · not tied to the strategy above</span>
      </div>
      {data?.answer ? (
        <p style={{ margin: 0, fontSize: 'var(--sa-text-md)', lineHeight: 1.55, color: 'var(--sa-text-2)', maxWidth: 900 }}>
          {data.answer}
        </p>
      ) : res.loading ? null : (
        <p style={{ margin: 0, fontSize: 'var(--sa-text-md)', lineHeight: 1.55, color: 'var(--sa-text-muted)' }}>
          No model calls in this period.
        </p>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '10px 16px', marginTop: 4 }}>
        <SegmentedControl
          label="Cost period"
          options={COST_PERIODS.map(([, l]) => l)}
          value={COST_PERIODS.find(([k]) => k === period)?.[1]}
          onChange={setPeriod}
        />
        {periodMeta && <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{periodMeta}</span>}
      </div>

      {res.failure && res.data === null && (
        <ErrorBanner what="model costs" failure={res.failure} updatedAt={res.updatedAt} onRetry={res.reload} />
      )}

      {res.loading && !data && (
        <div className="sa-loading" aria-hidden="true">
          <SkeletonTable
            columns={['Purpose', 'Calls', 'Input', 'Cached', 'Cost'].map((label, i) => ({ label, align: i === 0 ? 'left' : 'right' }))}
            rows={5}
            track="2fr 0.7fr 0.9fr 0.9fr 0.7fr"
          />
          <SkeletonTiles count={2} />
        </div>
      )}

      {data && (
        <>
          <Card
            id="model-costs"
            label="By purpose"
            refreshing={res.refreshing}
            style={{ marginTop: 4 }}
            right="calls, tokens and cost · caps reset on the 1st"
          >
            <div style={{ overflowX: 'auto', minWidth: 0 }}>
              <table className="mc-table" style={{ width: '100%', minWidth: 1000, borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th scope="col" style={{ ...thStyle, textAlign: 'left' }}>Purpose · model</th>
                    <th scope="col" style={thStyle}>Calls</th>
                    <th scope="col" style={thStyle} title="Calls divided by the sessions in the period">Per session</th>
                    <th scope="col" style={thStyle} title="All input tokens, cached and not">Input</th>
                    <th scope="col" style={thStyle} title="Input read from the provider's prompt cache, priced at the model's cached_input, and its share of input">Cached</th>
                    <th scope="col" style={thStyle} title="Anthropic cache-creation input, priced at the model's cache_write">Cache write</th>
                    <th scope="col" style={thStyle} title="Output tokens, thinking included">Output</th>
                    <th scope="col" style={thStyle}>Cost</th>
                    <th scope="col" style={thStyle} title="What cached reads saved, minus what cache writes cost extra, against the plain input price">Cache saved</th>
                    <th scope="col" style={{ ...thStyle, textAlign: 'left' }}>Monthly cap</th>
                  </tr>
                </thead>
                <tbody>
                  {data.purposes.map((row) => {
                    const href = inspectorHref(row.key, data.period.from, data.period.to);
                    const isTotal = row.key === 'total';
                    return (
                      <tr key={row.key} style={isTotal ? { borderTop: '2px solid var(--sa-border-strong)' } : undefined}>
                        <td style={tdStyle} data-l="">
                          <div style={{ fontWeight: 600, color: 'var(--sa-text)' }}>
                            {href ? (
                              <a href={href} title="Open these calls in the LLM inspector" style={{ color: 'var(--sa-text)', textDecoration: 'underline', textDecorationColor: 'var(--sa-border-strong)', textUnderlineOffset: 3 }}>
                                {row.label}
                              </a>
                            ) : (
                              row.label
                            )}
                          </div>
                          {row.models.length > 0 && (
                            <div style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', marginTop: 4 }}>
                              {row.models.join(' · ')}
                            </div>
                          )}
                          {purposeNote(row) && <div style={subNote}>{purposeNote(row)}</div>}
                        </td>
                        <td style={numTd} data-l="Calls">{fmtInt(row.calls)}</td>
                        <td style={numTd} data-l="Per session">{row.per_session ?? '—'}</td>
                        <td style={numTd} data-l="Input">{row.tokens_in ? fmtTokens(row.tokens_in) : '—'}</td>
                        <td style={numTd} data-l="Cached">
                          {row.tokens_cached_in ? fmtTokens(row.tokens_cached_in) : '—'}
                          {row.cached_share != null && row.tokens_cached_in > 0 && (
                            <div style={{ fontSize: 'var(--sa-text-xs)', color: 'var(--sa-text-muted)', marginTop: 2 }}>{Math.round(row.cached_share * 100)}%</div>
                          )}
                        </td>
                        <td style={numTd} data-l="Cache write">{row.tokens_cache_write_in ? fmtTokens(row.tokens_cache_write_in) : '—'}</td>
                        <td style={numTd} data-l="Output">{row.tokens_out ? fmtTokens(row.tokens_out) : '—'}</td>
                        <td style={{ ...numTd, color: 'var(--sa-text)', fontWeight: 600 }} data-l="Cost">
                          {row.cost_unknown && row.cost == null ? '—' : fmtUsd(row.cost)}
                        </td>
                        <td style={{ ...numTd, color: row.cache_saved && Number(row.cache_saved) > 0 ? 'var(--sa-positive)' : 'var(--sa-text-2)' }} data-l="Cache saved">
                          {row.cache_saved && Number(row.cache_saved) > 0 ? fmtUsd(row.cache_saved) : '—'}
                        </td>
                        <td style={tdStyle} data-l="Monthly cap">
                          <CapCell row={row} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p style={{ margin: 0, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.55 }}>
              Each call stores its cost at the prices of the day it ran, so a price change never rewrites this table. Cached input is read from the provider's prompt cache at the model's cached price; a cache write is Anthropic storing the prompt for later reads, at its write price. Calls stored before the cache split was recorded count as uncached and say so in the inspector. Click a purpose to see its calls in the LLM inspector.
            </p>
          </Card>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)', marginTop: 12 }}>
            <Card
              label="By model"
              refreshing={res.refreshing}
              right="prices from the models block in config"
            >
              <div style={{ overflowX: 'auto', minWidth: 0 }}>
                <table className="mc-table" style={{ width: '100%', minWidth: 700, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      <th scope="col" style={{ ...thStyle, textAlign: 'left' }}>Model · used for</th>
                      <th scope="col" style={thStyle}>Calls</th>
                      <th scope="col" style={thStyle}>Input</th>
                      <th scope="col" style={thStyle}>Cached</th>
                      <th scope="col" style={thStyle}>Cache write</th>
                      <th scope="col" style={thStyle}>Output</th>
                      <th scope="col" style={thStyle}>Cost</th>
                      <th scope="col" style={thStyle}>Per call</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.models.map((m) => (
                      <tr key={m.id}>
                        <td style={tdStyle} data-l="">
                          <div style={{ fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text)' }}>{m.id}</div>
                          <div style={subNote}>
                            {m.provider ? `${m.provider} · ` : ''}
                            {m.purposes.map((p) => PURPOSE_LABELS[p] ?? p).join(', ') || '—'}
                          </div>
                          <PricesLine row={m} />
                        </td>
                        <td style={numTd} data-l="Calls">{fmtInt(m.calls)}</td>
                        <td style={{ ...numTd, color: 'var(--sa-text-2)' }} data-l="Input">{m.tokens_in ? fmtTokens(m.tokens_in) : '—'}</td>
                        <td style={{ ...numTd, color: 'var(--sa-text-2)' }} data-l="Cached">{m.tokens_cached_in ? fmtTokens(m.tokens_cached_in) : '—'}</td>
                        <td style={{ ...numTd, color: 'var(--sa-text-2)' }} data-l="Cache write">{m.tokens_cache_write_in ? fmtTokens(m.tokens_cache_write_in) : '—'}</td>
                        <td style={{ ...numTd, color: 'var(--sa-text-2)' }} data-l="Output">{m.tokens_out ? fmtTokens(m.tokens_out) : '—'}</td>
                        <td style={{ ...numTd, color: 'var(--sa-text)', fontWeight: 600 }} data-l="Cost">{m.cost_unknown && m.cost == null ? '—' : fmtUsd(m.cost)}</td>
                        <td style={{ ...numTd, color: 'var(--sa-text-2)' }} data-l="Per call">{fmtPerCall(m.per_call)}</td>
                      </tr>
                    ))}
                    {data.models.length === 0 && (
                      <tr><td colSpan={8} style={{ ...tdStyle, color: 'var(--sa-text-muted)' }}>No model calls in this period.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              <p style={{ margin: 0, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.55 }}>
                Per call is cost ÷ calls. An Ask question takes several calls: one per lookup round.
              </p>
            </Card>

            <Card
              id="cost-per-session"
              label="Cost per session"
              refreshing={res.refreshing}
              right={`${costPeriodLabel} · per ${data.per_session_unit} · backtests not included`}
            >
              {data.per_session.length === 0 ? (
                <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>No model calls in this period.</div>
              ) : (
                <SessionChart
                  rows={data.per_session}
                  unit={data.per_session_unit}
                  summary={data.per_session_summary}
                />
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

function monthName(iso: string): string {
  const names = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  return names[Number(iso.slice(5, 7)) - 1] ?? '';
}
