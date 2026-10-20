import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { useLiveUpdates } from '../api/liveContext';
import {
  getApprovals,
  getChart,
  getDashboard,
  getPositions,
  getRiskBudget,
  getWatchlist,
} from '../api/state';
import type {
  ApprovalsResponse,
  ChartResponse,
  DashboardFeedItem,
  DashboardResponse,
  MarketClosedCard,
  PositionsResponse,
  RiskBudgetResponse,
  WatchlistResponse,
} from '../types/api';
import { ApprovalCard } from '../components/ApprovalCard';
import { BriefingCard } from '../components/BriefingCard';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import { CycleCounter } from '../components/CycleCounter';
import { PriceChart } from '../components/PriceChart';
import { RiskBudgetCard } from '../components/RiskBudgetCard';
import { StatCard } from '../components/StatCard';
import { Watchlist } from '../components/Watchlist';
import { formatDayChange, formatPrice } from '../components/watchlistFormat';
import { displayFullDate, displayTime, nyTime } from '../format';
import { actionBadgeStyle } from '../components/decisionFormat';
import { earningsChipLabel } from './decisionDetail';
import { actionChip, feedText } from './dashboardFeed';

function Feed({ items }: { items: DashboardFeedItem[] }) {
  const location = useLocation();
  // Group by cycle time like the design's "Today's decisions".
  const groups = useMemo(() => {
    const map = new Map<string, DashboardFeedItem[]>();
    for (const item of items) {
      const key = displayTime(item.time);
      map.set(key, [...(map.get(key) ?? []), item]);
    }
    return [...map.entries()].reverse();
  }, [items]);

  return (
    <Card label="Today's decisions" right={<Link to="/inspector">All model calls</Link>}>
      {groups.length === 0 && (
        <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
          No decisions yet today.
        </div>
      )}
      {groups.map(([time, rows]) => (
        <div
          key={time}
          style={{
            display: 'flex',
            gap: 'var(--sa-space-3)',
            padding: 'var(--sa-space-2) 0',
            borderBottom: '1px solid var(--sa-border)',
            alignItems: 'baseline',
          }}
        >
          <span
            style={{
              fontFamily: 'var(--sa-font-mono)',
              fontSize: 'var(--sa-text-md)',
              color: 'var(--sa-text-muted)',
              flex: '0 0 46px',
            }}
          >
            {time}
          </span>
          <span style={{ display: 'flex', flexWrap: 'wrap', gap: '6px var(--sa-space-3)' }}>
            {rows.map((row) => {
              const chip = actionChip(row.action, row.no_call_reason);
              return (
                <Link
                  key={row.id}
                  to={`/decisions/${row.id}`}
                  state={{ backgroundLocation: location }}
                  style={{ color: 'var(--sa-text-2)', display: 'inline-flex', gap: '6px', alignItems: 'center' }}
                >
                  <Chip variant={chip.variant}>{chip.label}</Chip>
                  <span style={{ fontSize: 'var(--sa-text-md)' }}>{feedText(row)}</span>
                </Link>
              );
            })}
          </span>
        </div>
      ))}
    </Card>
  );
}

function toneStyle(tone: number): React.CSSProperties {
  return {
    padding: '4px 8px',
    borderRadius: 'var(--sa-radius-sm)',
    fontFamily: 'var(--sa-font-mono)',
    fontSize: 'var(--sa-text-sm)',
    whiteSpace: 'nowrap',
    background:
      tone > 0
        ? 'var(--sa-positive-bg)'
        : tone < 0
          ? 'var(--sa-negative-bg)'
          : 'var(--sa-raised)',
    color:
      tone > 0
        ? 'var(--sa-positive)'
        : tone < 0
          ? 'var(--sa-negative-text)'
          : 'var(--sa-text-2)',
  };
}

function LatestDecision({ dashboard }: { dashboard: DashboardResponse | null }) {
  const location = useLocation();
  const d = dashboard?.latest_decision ?? null;
  if (!d) {
    return (
      <Card label="Latest decision">
        <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
          No decision yet this session.
        </div>
      </Card>
    );
  }
  const chip = actionChip(d.final_action, d.no_call_reason);
  const badge = actionBadgeStyle(d.final_action);
  const regimeVariant: 'positive' | 'negative' | 'neutral' =
    d.regime?.trend === 'up'
      ? 'positive'
      : d.regime?.trend === 'down'
        ? 'negative'
        : 'neutral';
  return (
    <Card
      label="Latest decision"
      right={
        <span
          style={{
            fontFamily: 'var(--sa-font-mono)',
            fontSize: 'var(--sa-text-sm)',
            color: 'var(--sa-text-muted)',
          }}
        >
          {displayTime(d.time)} · {d.model ?? '—'}
        </span>
      }
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sa-space-3)' }}>
        <div
          style={{
            minWidth: '88px',
            height: '48px',
            padding: '0 14px',
            borderRadius: 'var(--sa-radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontFamily: 'var(--sa-font-mono)',
            fontWeight: 600,
            fontSize: 'var(--sa-text-lg)',
            whiteSpace: 'nowrap',
            background: badge.bg,
            color: badge.color,
          }}
        >
          {chip.label}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--sa-text-lg)' }}>{d.symbol}</span>
          <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
            confidence{' '}
            <span style={{ fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text-2)' }}>
              {d.confidence !== null ? d.confidence.toFixed(2) : '—'}
            </span>
          </span>
        </div>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
        {d.regime && (
          <span title="SPY regime: trend × volatility">
            <Chip variant={regimeVariant}>
              market {d.regime.trend} · {d.regime.volatility} vol
            </Chip>
          </span>
        )}
        <Chip variant="neutral">{earningsChipLabel(d.earnings)}</Chip>
      </div>
      {d.why && (
        <p style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.55 }}>
          {d.why}
        </p>
      )}
      {d.sig.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {d.sig.map((s, i) => (
            <span key={i} style={toneStyle(s.tone)}>
              {s.text}
            </span>
          ))}
        </div>
      )}
      {d.box.length > 0 && (
        <div
          style={{
            border: '1px solid var(--sa-border)',
            borderRadius: 'var(--sa-radius-md)',
            padding: '4px var(--sa-space-3)',
          }}
        >
          {d.box.map((row, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: 'var(--sa-space-3)',
                padding: '7px 0',
                fontSize: 'var(--sa-text-md)',
                borderBottom:
                  i < d.box.length - 1 ? '1px solid var(--sa-border)' : 'none',
              }}
            >
              <span style={{ color: 'var(--sa-text-2)' }}>{row.label}</span>
              <span
                style={{
                  fontFamily: 'var(--sa-font-mono)',
                  fontSize: 'var(--sa-text-sm)',
                  textAlign: 'right',
                  color:
                    row.tone > 0
                      ? 'var(--sa-positive)'
                      : row.tone < 0
                        ? 'var(--sa-negative-text)'
                        : 'var(--sa-text-2)',
                }}
              >
                {row.value}
              </span>
            </div>
          ))}
        </div>
      )}
      <Link
        to={`/decisions/${d.id}`}
        state={{ backgroundLocation: location }}
        style={{
          height: '44px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 'var(--sa-radius-md)',
          border: '1px solid var(--sa-border-strong)',
          background: 'var(--sa-raised)',
          color: 'var(--sa-text)',
          textDecoration: 'none',
          fontWeight: 500,
          marginTop: 'auto',
        }}
      >
        Open full decision
      </Link>
    </Card>
  );
}

function OpenPositionsMini({ positions }: { positions: PositionsResponse | null }) {
  const open = positions?.open ?? [];
  return (
    <Card
      label="Open positions"
      right={<Link to="/positions">Positions & orders</Link>}
    >
      {open.length === 0 ? (
        <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
          No open positions.
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', minWidth: '560px' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
                {['Symbol', 'Qty', 'Entry', 'Last', 'Stop', 'Stop distance', 'Unrealized'].map((h, i) => (
                  <th
                    key={h}
                    style={{
                      padding: 'var(--sa-space-2) 10px',
                      textAlign: i === 0 ? 'left' : 'right',
                      fontSize: 'var(--sa-text-sm)',
                      color: 'var(--sa-text-muted)',
                      fontWeight: 600,
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {open.map((p) => (
                <tr key={p.id} style={{ borderBottom: '1px solid var(--sa-border)' }}>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontWeight: 600 }}>
                    {p.symbol}
                    {p.opened_today && (
                      <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}> · today</span>
                    )}
                    {p.deferred_exit && (
                      <span style={{ marginLeft: 'var(--sa-space-2)' }}>
                        <Chip variant="warning">exit deferred</Chip>
                      </span>
                    )}
                  </td>
                  {[
                    String(p.qty),
                    p.avg_entry_price,
                    p.last_price ?? '—',
                    p.current_stop_price ?? '—',
                    p.stop_distance_label ?? '—',
                    p.unrealized_pnl ?? '—',
                  ].map((cell, i) => (
                    <td
                      key={i}
                      style={{
                        padding: 'var(--sa-space-2) 10px',
                        textAlign: 'right',
                        fontFamily: 'var(--sa-font-mono)',
                        fontSize: 'var(--sa-text-md)',
                        color: 'var(--sa-text-2)',
                      }}
                    >
                      {cell}
                      {i === 5 && p.r_multiple && (
                        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                          {Number(p.r_multiple) >= 0 ? '+' : ''}{p.r_multiple}R
                        </div>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function MarketClosed({ mc }: { mc: MarketClosedCard }) {
  return (
    <Card>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          gap: 'var(--sa-space-3)',
          maxWidth: '760px',
        }}
      >
        <span style={{ color: 'var(--sa-text-muted)' }}>
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <polyline points="12 7 12 12 15 14" />
          </svg>
        </span>
        <h3 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>Market closed</h3>
        <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)' }}>
          {mc.next_open
            ? `The next session opens ${displayFullDate(mc.next_open)} at ${displayTime(mc.next_open)} Amsterdam (${nyTime(mc.next_open)} New York). `
            : 'The next session is not on the calendar yet. '}
          {mc.first_cycle && `The first cycle runs at ${displayTime(mc.first_cycle)}. `}
          {mc.open_count > 0
            ? `All ${mc.open_count} position${mc.open_count === 1 ? ' is' : 's are'} protected by stops at Alpaca overnight; a stop only triggers once trading opens.`
            : 'No open positions.'}
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-2)' }}>
          {mc.stops.map((s) => (
            <Chip key={s.symbol} variant="neutral">
              {s.symbol} stop {s.stop}
            </Chip>
          ))}
          {mc.today_summary && <Chip variant="info">today: {mc.today_summary}</Chip>}
        </div>
      </div>
    </Card>
  );
}

export default function Dashboard() {
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistResponse | null>(null);
  const [approvals, setApprovals] = useState<ApprovalsResponse | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [chart, setChart] = useState<ChartResponse | null>(null);
  const [budget, setBudget] = useState<RiskBudgetResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const refreshDashboard = useCallback((symbol?: string | null) => {
    getDashboard(symbol ?? undefined)
      .then(setDashboard)
      .catch(console.error);
  }, []);
  const refreshWatchlist = useCallback(() => {
    getWatchlist().then(setWatchlist).catch(console.error);
  }, []);
  const refreshApprovals = useCallback(() => {
    getApprovals().then(setApprovals).catch(console.error);
  }, []);
  const refreshPositions = useCallback(() => {
    getPositions().then(setPositions).catch(console.error);
  }, []);
  const refreshChart = useCallback((symbol: string | null) => {
    if (!symbol) return;
    getChart(symbol).then(setChart).catch(() => setChart(null));
  }, []);
  const refreshBudget = useCallback(() => {
    getRiskBudget().then(setBudget).catch(console.error);
  }, []);

  const symbols = watchlist?.rows.map((r) => r.symbol) ?? [];
  const chartSymbol = selected ?? symbols[0] ?? null;

  useEffect(() => {
    refreshWatchlist();
    refreshApprovals();
    refreshPositions();
    refreshBudget();
  }, [refreshWatchlist, refreshApprovals, refreshPositions, refreshBudget]);
  useEffect(() => {
    // The latest-decision card tracks the selected symbol.
    refreshDashboard(chartSymbol);
  }, [chartSymbol, refreshDashboard]);
  useEffect(() => {
    refreshChart(chartSymbol);
  }, [chartSymbol, refreshChart]);

  useLiveUpdates(['decisions', 'header', 'performance', 'reviews'], () =>
    refreshDashboard(chartSymbol),
  );
  useLiveUpdates(['watchlist'], refreshWatchlist);
  useLiveUpdates(['approvals'], () => {
    refreshApprovals();
    refreshDashboard(chartSymbol);
  });
  useLiveUpdates(['positions', 'orders'], () => {
    refreshPositions();
    refreshDashboard(chartSymbol);
    refreshChart(chartSymbol);
  });
  // The risk budget card reads the gate context: positions, orders and
  // account updates (header) all change its inputs.
  useLiveUpdates(['positions', 'orders', 'header'], refreshBudget);
  useLiveUpdates(['watchlist', 'decisions'], () => refreshChart(chartSymbol));

  const stats = dashboard?.stats;
  const sincePct =
    stats?.since_start_pct !== null && stats?.since_start_pct !== undefined
      ? Number(stats.since_start_pct)
      : null;

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      {approvals && approvals.items.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
          {approvals.items.map((a) => (
            <ApprovalCard
              key={a.id}
              approval={a}
              killSwitchOn={approvals.kill_switch_on}
              paused={approvals.paused}
              onResolved={() => {
                refreshApprovals();
                refreshDashboard();
                refreshPositions();
              }}
            />
          ))}
        </div>
      )}

      {(dashboard?.cycle_line || dashboard?.cycle_counter) && (
        <div
          role="status"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: '10px var(--sa-space-3)',
            padding: 'var(--sa-space-3) var(--sa-space-4)',
            background: 'var(--sa-panel)',
            border: '1px solid var(--sa-border)',
            borderRadius: 'var(--sa-radius-lg)',
            fontSize: 'var(--sa-text-md)',
          }}
        >
          {dashboard?.cycle_line && (
            <>
              <span
                style={{
                  padding: '3px var(--sa-space-2)',
                  borderRadius: 'var(--sa-radius-sm)',
                  background: 'var(--sa-subtle)',
                  color: 'var(--sa-text-2)',
                  fontSize: 'var(--sa-text-sm)',
                  fontWeight: 600,
                }}
              >
                {dashboard.cycle_line.label}
              </span>
              <span
                style={{ flex: '1 1 420px', color: 'var(--sa-text-2)', lineHeight: 1.5 }}
              >
                {dashboard.cycle_line.text}
              </span>
            </>
          )}
          {dashboard?.cycle_counter && <CycleCounter counter={dashboard.cycle_counter} />}
        </div>
      )}

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--sa-space-3)',
          alignItems: 'stretch',
        }}
      >
        <StatCard
          label="Positions"
          value={`${stats?.positions ?? 0} of ${stats?.positions_cap ?? '—'}`}
          sub={`cash $${stats?.cash ?? '—'} · no margin`}
        />
        <Link
          to="/inspector"
          title={
            stats
              ? `Decision calls today: at most ${stats.calls_per_cycle} per cycle, ${stats.cycles_today} cycles.` +
                (stats.reviews_cost_month !== null && stats.reviews_cost_month !== undefined
                  ? ` Reviews have their own $${stats.reviews_cost_cap} monthly cap: $${Number(stats.reviews_cost_month).toFixed(2)} used.`
                  : '') +
                (stats.shadows_cost_month !== null && stats.shadows_cost_month !== undefined
                  ? ` Shadows have their own $${stats.shadows_cost_cap} monthly cap: $${Number(stats.shadows_cost_month).toFixed(2)} used.`
                  : '')
              : undefined
          }
          style={{
            display: 'flex',
            flex: '1 1 190px',
            minWidth: 0,
            textDecoration: 'none',
          }}
        >
          <StatCard
            label="Model calls today"
            value={`${stats?.calls_today ?? 0}`}
            bar={{
              width: `${stats && stats.calls_max > 0 ? Math.min(100, (stats.calls_today / stats.calls_max) * 100) : 0}%`,
              color: 'var(--sa-info)',
            }}
            sub={`of ${stats?.calls_max ?? '—'} max · $${stats ? Number(stats.calls_cost_today).toFixed(3) : '—'} today · $${stats ? Number(stats.calls_cost_month).toFixed(2) : '—'} this month`}
            style={{ flex: '1 1 auto', width: '100%', cursor: 'pointer' }}
          />
        </Link>
        <StatCard
          label="Since start"
          value={
            stats?.since_start_pct !== null && stats?.since_start_pct !== undefined
              ? `${stats.since_start_pct}%`
              : '—'
          }
          valueColor={
            sincePct === null
              ? undefined
              : sincePct > 0
                ? 'var(--sa-positive)'
                : sincePct < 0
                  ? 'var(--sa-negative)'
                  : undefined
          }
          sub={`SPY ${stats?.spy_pct ?? '—'}% · ${stats?.since_label ?? ''}`}
        />
      </div>

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--sa-space-5)',
          alignItems: 'stretch',
        }}
      >
        <BriefingCard briefing={dashboard?.briefing ?? null} />
        <RiskBudgetCard budget={budget} />
      </div>

      {dashboard?.market_closed && (
        <MarketClosed mc={dashboard.market_closed} />
      )}

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--sa-space-4)',
          alignItems: 'stretch',
        }}
      >
        <div style={{ flex: '2 1 560px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <Card
            label={chartSymbol ?? 'Chart'}
            right={
              <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                1h bars · last 5 sessions · SIP, 15 min delayed
              </span>
            }
          >
            <div style={{ display: 'flex', gap: 'var(--sa-space-2)', flexWrap: 'wrap' }}>
              {symbols.map((symbol) => (
                <button
                  key={symbol}
                  type="button"
                  onClick={() => setSelected(symbol)}
                  style={{
                    height: '30px',
                    padding: '0 var(--sa-space-3)',
                    borderRadius: 'var(--sa-radius-sm)',
                    border: `1px solid ${symbol === chartSymbol ? 'var(--sa-accent-border)' : 'var(--sa-border)'}`,
                    background: symbol === chartSymbol ? 'var(--sa-accent-bg)' : 'var(--sa-raised)',
                    color: symbol === chartSymbol ? 'var(--sa-accent)' : 'var(--sa-text-2)',
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-sm)',
                    cursor: 'pointer',
                  }}
                >
                  {symbol}
                </button>
              ))}
            </div>
            {chart && (
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--sa-space-3)' }}>
                <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-price)', fontWeight: 500 }}>
                  {formatPrice(chart.price)}
                </span>
                <span
                  style={{
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-md)',
                    color:
                      chart.day_change_pct !== null && Number(chart.day_change_pct) >= 0
                        ? 'var(--sa-positive)'
                        : 'var(--sa-negative-text)',
                  }}
                >
                  {formatDayChange(chart.day_change_pct)}
                </span>
                {chart.warming_up && <Chip variant="info">Warming up</Chip>}
              </div>
            )}
            {chart && chart.bars.length > 0 ? (
              <PriceChart bars={chart.bars} levels={chart.levels} />
            ) : (
              <div style={{ height: '340px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--sa-text-muted)' }}>
                No bars loaded for {chartSymbol ?? 'this symbol'} yet.
              </div>
            )}
            <div style={{ display: 'flex', gap: 'var(--sa-space-4)', flexWrap: 'wrap', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              <Legend swatch="var(--sa-chart-ema20)" label="EMA 20" />
              <Legend swatch="var(--sa-chart-ema50)" label="EMA 50" />
              <Legend swatch="var(--sa-chart-vwap)" label="Session VWAP" />
              {chart?.levels.entry && <Legend swatch="var(--sa-text-2)" label="Entry" />}
              {chart?.levels.stop && <Legend swatch="var(--sa-negative)" label="Stop" />}
              {chart?.levels.target && <Legend swatch="var(--sa-positive)" label="Target" />}
            </div>
          </Card>
        </div>
        <div style={{ flex: '1 1 300px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <LatestDecision dashboard={dashboard} />
        </div>
      </div>

      <Watchlist
        rows={watchlist?.rows ?? []}
        selected={chartSymbol}
        onSelect={setSelected}
        universeMode={watchlist?.universe_mode ?? 'fixed'}
        watchlistRows={watchlist?.watchlist_rows ?? 15}
        earningsBlackoutDays={watchlist?.earnings_blackout_days ?? 3}
      />

      <OpenPositionsMini positions={positions} />

      <Feed items={dashboard?.feed ?? []} />
    </div>
  );
}

function Legend({ swatch, label }: { swatch: string; label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
      <span style={{ width: '14px', height: '3px', borderRadius: '2px', background: swatch }} />
      {label}
    </span>
  );
}
