import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { useLiveUpdates } from '../api/liveContext';
import {
  getApprovals,
  getChart,
  getDashboard,
  getPositions,
  getWatchlist,
} from '../api/state';
import type {
  ApprovalsResponse,
  ChartResponse,
  DashboardFeedItem,
  DashboardResponse,
  MarketClosedCard,
  PositionsResponse,
  WatchlistResponse,
} from '../types/api';
import { ApprovalCard } from '../components/ApprovalCard';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import { PriceChart } from '../components/PriceChart';
import { StatCard } from '../components/StatCard';
import { Watchlist } from '../components/Watchlist';
import { formatDayChange, formatPrice } from '../components/watchlistFormat';
import { displayFullDate, displayTime, nyTime } from '../format';
import { actionChip, feedText } from './dashboardFeed';

function Feed({ items }: { items: DashboardFeedItem[] }) {
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

function LatestDecision({ dashboard }: { dashboard: DashboardResponse | null }) {
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
  return (
    <Card
      label="Latest decision"
      right={<Link to={`/decisions/${d.id}`}>Open decision detail</Link>}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sa-space-3)' }}>
        <span style={{ fontWeight: 600 }}>{d.symbol}</span>
        <Chip variant={chip.variant}>{chip.label}</Chip>
        {d.confidence !== null && (
          <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            confidence {d.confidence}
          </span>
        )}
      </div>
      {d.why && (
        <p style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
          {d.why}
        </p>
      )}
      <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        {displayTime(d.time)} cycle · {d.context}
      </div>
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
  const [selected, setSelected] = useState<string | null>(null);

  const refreshDashboard = useCallback(() => {
    getDashboard().then(setDashboard).catch(console.error);
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

  const symbols = watchlist?.rows.map((r) => r.symbol) ?? [];
  const chartSymbol = selected ?? symbols[0] ?? null;

  useEffect(() => {
    refreshDashboard();
    refreshWatchlist();
    refreshApprovals();
    refreshPositions();
  }, [refreshDashboard, refreshWatchlist, refreshApprovals, refreshPositions]);
  useEffect(() => {
    refreshChart(chartSymbol);
  }, [chartSymbol, refreshChart]);

  useLiveUpdates(['decisions', 'header', 'performance'], refreshDashboard);
  useLiveUpdates(['watchlist'], refreshWatchlist);
  useLiveUpdates(['approvals'], () => {
    refreshApprovals();
    refreshDashboard();
  });
  useLiveUpdates(['positions', 'orders'], () => {
    refreshPositions();
    refreshDashboard();
    refreshChart(chartSymbol);
  });
  useLiveUpdates(['watchlist', 'decisions'], () => refreshChart(chartSymbol));

  const stats = dashboard?.stats;
  const riskPct = stats?.open_risk_pct !== null && stats?.open_risk_pct !== undefined
    ? Number(stats.open_risk_pct)
    : null;
  const riskCap = stats?.open_risk_cap_pct ?? 0;

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      {approvals && approvals.items.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
          {approvals.items.map((a) => (
            <ApprovalCard
              key={a.id}
              approval={a}
              killSwitchOn={approvals.kill_switch_on}
              onResolved={() => {
                refreshApprovals();
                refreshDashboard();
                refreshPositions();
              }}
            />
          ))}
        </div>
      )}

      {dashboard?.cycle_line && (
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 'var(--sa-space-2) var(--sa-space-5)',
            justifyContent: 'space-between',
            fontSize: 'var(--sa-text-md)',
          }}
        >
          <span>
            <b style={{ fontWeight: 600 }}>{dashboard.cycle_line.label}</b>
            <span style={{ color: 'var(--sa-text-2)' }}> · {dashboard.cycle_line.text}</span>
          </span>
          <span style={{ color: 'var(--sa-text-muted)', fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)' }}>
            Cycle {dashboard.cycle_line.cycle_index} of {dashboard.cycle_line.cycles_today} today
          </span>
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-4)' }}>
        <StatCard
          label="Open risk"
          value={riskPct !== null ? `${riskPct.toFixed(2)}%` : '—'}
          sub={`of ${riskCap}% cap${stats?.open_risk_usd ? ` · $${stats.open_risk_usd}` : ''}`}
          bar={
            riskPct !== null && riskCap > 0
              ? {
                  width: `${Math.min(100, (riskPct / riskCap) * 100)}%`,
                  color: 'var(--sa-accent)',
                }
              : undefined
          }
        />
        <StatCard
          label="Positions"
          value={`${stats?.positions ?? 0} of ${stats?.positions_cap ?? '—'}`}
          sub={`cash $${stats?.cash ?? '—'} · no margin`}
        />
        <StatCard
          label="Day trades"
          value={`${stats?.day_trades ?? 0} of ${stats?.day_trades_max ?? '—'}`}
          sub="last 5 trading days"
        />
        <StatCard
          label="Since start"
          value={stats?.since_start_pct !== null && stats?.since_start_pct !== undefined ? `${stats.since_start_pct}%` : '—'}
          sub={`SPY ${stats?.spy_pct ?? '—'}% · ${stats?.since_label ?? ''}`}
        />
      </div>

      {dashboard?.market_closed ? (
        <MarketClosed mc={dashboard.market_closed} />
      ) : (
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
      )}

      <Watchlist
        rows={watchlist?.rows ?? []}
        selected={chartSymbol}
        onSelect={setSelected}
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
