import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';

import { useLiveUpdates } from '../api/liveContext';
import {
  getApprovals,
  getChart,
  getDashboard,
  getPositions,
  getRiskBudget,
  getWatchlist,
} from '../api/state';
import { useApiResource } from '../api/useResource';
import type { FetchFailure, Resource } from '../api/useResource';
import type {
  DashboardFeedItem,
  DashboardLatestDecision,
  DashboardResponse,
  MarketClosedCard,
  PositionsResponse,
} from '../types/api';
import { ApprovalCard } from '../components/ApprovalCard';
import { Banner } from '../components/Banner';
import { BriefingCard } from '../components/BriefingCard';
import { Card } from '../components/Card';
import { CardBody, ErrorBanner } from '../components/cardState';
import { Chip } from '../components/Chip';
import { CycleCounter } from '../components/CycleCounter';
import { DialogButton, Modal } from '../components/Modal';
import { PriceChart } from '../components/PriceChart';
import { RiskBudgetCard } from '../components/RiskBudgetCard';
import { StatCard } from '../components/StatCard';
import { Watchlist } from '../components/Watchlist';
import { formatDayChange, formatPrice } from '../components/watchlistFormat';
import {
  displayFullDate,
  displayTime,
  displayWeekdayDateTime,
  displayWeekdayTime,
  nyTime,
  sessionDayLabel,
  sessionDayShort,
  signed,
  signedPct,
  signedUsd,
} from '../format';
import {
  actionBadgeStyle,
  formatAmsTime,
  formatNyTitle,
} from '../components/decisionFormat';
import { earningsChipLabel } from './decisionDetail';
import {
  actionText,
  collapsedMeta,
  cycleMeta,
  groupFeed,
  rowSymbols,
  type FeedCycle,
  type FeedRow,
} from './dashboardFeed';

/** One feed row: a single decision links to the decision drawer, a grouped
 * row to the LLM inspector filtered to its cycle and action. */
function FeedRowLink({ row }: { row: FeedRow }) {
  const location = useLocation();
  const { text, more } = rowSymbols(row.items);
  const single = row.items.length === 1;
  const item = row.items[0];
  const to = single
    ? `/decisions/${item.id}`
    : `/inspector?cycle=${encodeURIComponent(item.time)}&action=${row.action}`;
  return (
    <Link
      to={to}
      state={single ? { backgroundLocation: location } : undefined}
      className="sa-row"
      style={{
        display: 'grid',
        gridTemplateColumns: '108px minmax(0, 1fr) auto',
        gap: 'var(--sa-space-3)',
        alignItems: 'center',
        minHeight: 'var(--sa-control-height)',
        padding: '6px 8px',
        margin: '0 -8px',
        borderRadius: 'var(--sa-radius-sm)',
        borderBottom: '1px solid var(--sa-border)',
        fontSize: 'var(--sa-text-md)',
        color: 'var(--sa-text-2)',
        textDecoration: 'none',
      }}
    >
      <span
        style={{
          display: 'inline-block',
          minWidth: '56px',
          fontFamily: 'var(--sa-font-mono)',
          fontSize: 'var(--sa-text-sm)',
          fontWeight: 600,
          color: row.color,
          whiteSpace: 'nowrap',
        }}
      >
        {row.label}
      </span>
      <span style={{ minWidth: 0 }}>
        <b style={{ fontWeight: 600, color: 'var(--sa-text)' }}>{text}</b>
        {more > 0 && (
          <span style={{ color: 'var(--sa-text-muted)' }}> +{more} more</span>
        )}
        {row.reason && (
          <span style={{ color: 'var(--sa-text-muted)' }}> · {row.reason}</span>
        )}
      </span>
      <span
        style={{
          fontFamily: 'var(--sa-font-mono)',
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-text-muted)',
        }}
      >
        {row.items.length > 1 ? row.items.length : ''}
      </span>
    </Link>
  );
}

function CycleGroup({
  cycle,
  expanded,
  onToggle,
}: {
  cycle: FeedCycle;
  expanded: boolean;
  onToggle: () => void;
}) {
  const rows = expanded ? cycle.rows : cycle.changeRows;
  return (
    <div>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        style={{
          display: 'flex',
          width: '100%',
          alignItems: 'baseline',
          gap: 'var(--sa-space-3)',
          minHeight: '40px',
          padding: 'var(--sa-space-2) 0 var(--sa-space-1)',
          border: 0,
          background: 'transparent',
          color: 'var(--sa-text)',
          textAlign: 'left',
          cursor: 'pointer',
        }}
      >
        <span aria-hidden="true" style={{ color: 'var(--sa-text-muted)' }}>
          {expanded ? '▾' : '▸'}
        </span>
        <span
          title={formatNyTitle(cycle.time)}
          style={{
            fontFamily: 'var(--sa-font-mono)',
            fontSize: 'var(--sa-text-md)',
            fontWeight: 600,
          }}
        >
          {formatAmsTime(cycle.time)}
        </span>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          {expanded ? cycleMeta(cycle) : collapsedMeta(cycle)}
        </span>
      </button>
      {rows.map((row) => (
        <FeedRowLink key={row.key} row={row} />
      ))}
    </div>
  );
}

/** "Today's decisions" — feed grouped per cycle; the latest cycle opens,
 * earlier cycles collapse to their change rows. */
function Feed({ res }: { res: Resource<DashboardResponse> }) {
  const items = res.data?.feed ?? EMPTY_FEED;
  const cycles = useMemo(() => groupFeed(items), [items]);
  // The latest cycle starts open; every other starts collapsed to changes.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const isOpen = (cycle: FeedCycle, first: boolean) =>
    toggled[cycle.key] ?? first;
  const sess = res.data?.session;
  // Off-hours the feed is titled with the shown session's date
  // ("Friday 23 Oct · last session", docs/SPEC.md).
  const title =
    sess && !sess.shown_is_today
      ? `${sessionDayLabel(sess.shown_date)} · last session`
      : "Today's decisions";

  return (
    <Card
      label={title}
      right={<Link to="/inspector">All model calls</Link>}
      busy={res.loading}
      style={{ flex: 1 }}
    >
      <CardBody
        res={res}
        what="the decision feed"
        isEmpty={(d) => d.feed.length === 0}
        empty={
          <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
            {sess && !sess.shown_is_today
              ? `No decisions on ${sessionDayLabel(sess.shown_date)}.`
              : 'No decisions yet today — the next cycle writes the first.'}
          </div>
        }
      >
        {() => (
          <>
            {cycles.map((cycle, i) => (
              <CycleGroup
                key={cycle.key}
                cycle={cycle}
                expanded={isOpen(cycle, i === 0)}
                onToggle={() =>
                  setToggled((t) => ({ ...t, [cycle.key]: !isOpen(cycle, i === 0) }))
                }
              />
            ))}
            <div
              style={{
                fontSize: 'var(--sa-text-sm)',
                color: 'var(--sa-text-muted)',
                paddingTop: 'var(--sa-space-2)',
              }}
            >
              Changes (entries, exits, stop moves) always show. Other decisions are
              grouped by action and reason; collapsed cycles show their changes
              only.
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}

const EMPTY_FEED: DashboardFeedItem[] = [];

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

function LatestDecision({ res }: { res: Resource<DashboardResponse> }) {
  const d = res.data?.latest_decision ?? null;
  // Off-session fallback (design/screens/States.dc.html): the card shows
  // the most recent decision for the symbol, with its date.
  const compact = d !== null && !d.in_session;
  return (
    <Card
      label={compact ? `Latest decision · ${d.symbol}` : 'Latest decision'}
      busy={res.loading}
      right={
        d && (
          <span
            title={formatNyTitle(d.time)}
            style={{
              fontFamily: 'var(--sa-font-mono)',
              fontSize: 'var(--sa-text-sm)',
              color: 'var(--sa-text-muted)',
            }}
          >
            {displayTime(d.time)} · {d.model ?? '—'}
          </span>
        )
      }
    >
      <CardBody
        res={res}
        what="the latest decision"
        isEmpty={(data) => data.latest_decision === null}
        empty={
          <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
            No decisions recorded yet — the next cycle writes the first.
          </div>
        }
      >
        {() =>
          d === null ? null : compact ? (
            <LatestDecisionCompact d={d} />
          ) : (
            <LatestDecisionBody d={d} />
          )
        }
      </CardBody>
    </Card>
  );
}

/** Compact off-session card: "HOLD · Fri 23 Oct 21:46 · trend intact". */
function LatestDecisionCompact({ d }: { d: DashboardLatestDecision }) {
  const location = useLocation();
  const chip = actionText(d.final_action);
  const badge = actionBadgeStyle(d.final_action);
  return (
    <>
      <div
        style={{
          fontSize: 'var(--sa-text-md)',
          color: 'var(--sa-text-2)',
          lineHeight: 1.55,
        }}
      >
        <b
          style={{
            fontFamily: 'var(--sa-font-mono)',
            fontWeight: 600,
            color: badge.color,
          }}
        >
          {chip.label}
        </b>
        {' · '}
        <span title={formatNyTitle(d.time)}>
          {displayWeekdayDateTime(d.time)}
        </span>
        {d.why ? ` · ${d.why}` : ''}
      </div>
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
    </>
  );
}

function LatestDecisionBody({ d }: { d: DashboardLatestDecision }) {
  const location = useLocation();
  const chip = actionText(d.final_action);
  const badge = actionBadgeStyle(d.final_action);
  const regimeVariant: 'positive' | 'negative' | 'neutral' =
    d.regime?.trend === 'up'
      ? 'positive'
      : d.regime?.trend === 'down'
        ? 'negative'
        : 'neutral';
  return (
    <>
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
            {d.chosen_option && (
              <span style={{ color: 'var(--sa-accent)' }}> · option {d.chosen_option}</span>
            )}
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
    </>
  );
}

function OpenPositionsMini({
  res,
  nextCycle,
  nextCycleToday,
  passCount,
}: {
  res: Resource<PositionsResponse>;
  /** Next cycle's ISO time, or null when unknown. */
  nextCycle: string | null;
  nextCycleToday: boolean;
  passCount: number | null;
}) {
  const nextCycleLabel = nextCycle
    ? nextCycleToday
      ? displayTime(nextCycle)
      : displayWeekdayTime(nextCycle)
    : null;
  const open = res.data?.open ?? [];
  return (
    <Card
      label="Open positions"
      right={<Link to="/positions">Positions & orders</Link>}
      busy={res.loading}
      style={{ flex: 1 }}
    >
      <CardBody
        res={res}
        what="positions"
        isEmpty={(d) => d.open.length === 0}
        empty={
          <>
            <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)' }}>
              No open positions.
            </p>
            <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-muted)' }}>
              {nextCycleLabel ? (
                <>
                  The next cycle at{' '}
                  <span title={formatNyTitle(nextCycle ?? '')}>{nextCycleLabel}</span>{' '}
                  may open one
                  {passCount !== null && passCount > 0
                    ? `: ${passCount} symbol${passCount === 1 ? '' : 's'} pass${passCount === 1 ? 'es' : ''} the rules`
                    : ''}
                  .
                </>
              ) : (
                'The next entry decision comes with the next cycle.'
              )}
            </p>
          </>
        }
      >
        {() => (
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
                  </td>
                  {[
                    String(p.qty),
                    p.avg_entry_price,
                    p.last_price ?? '—',
                    p.current_stop_price ?? '—',
                    p.stop_distance_label ?? '—',
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
                    </td>
                  ))}
                  <td
                    style={{
                      padding: 'var(--sa-space-2) 10px',
                      textAlign: 'right',
                      fontFamily: 'var(--sa-font-mono)',
                      fontSize: 'var(--sa-text-md)',
                      color:
                        p.unrealized_pnl === null || p.unrealized_pnl === undefined
                          ? 'var(--sa-text-2)'
                          : p.unrealized_pnl > 0
                            ? 'var(--sa-positive)'
                            : p.unrealized_pnl < 0
                              ? 'var(--sa-negative-text)'
                              : 'var(--sa-text-2)',
                    }}
                  >
                    {p.unrealized_pnl === null || p.unrealized_pnl === undefined
                      ? '—'
                      : signedUsd(p.unrealized_pnl)}
                    {(() => {
                      const sub =
                        p.r_multiple !== null && p.r_multiple !== undefined
                          ? `${signed(p.r_multiple)}R`
                          : p.stop_distance_label === 'stop above entry'
                            ? 'stop above entry'
                            : null;
                      return (
                        sub && (
                          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                            {sub}
                          </div>
                        )
                      );
                    })()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
      </CardBody>
    </Card>
  );
}

export function MarketClosed({
  mc,
  day,
}: {
  mc: MarketClosedCard;
  /** Session date the summary covers — "Friday 23 Oct". Defaults to "today". */
  day?: string;
}) {
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
          {mc.today_summary && (
            <Chip variant="info">
              {day ? `${sessionDayShort(day)}` : 'today'}: {mc.today_summary}
            </Chip>
          )}
        </div>
      </div>
    </Card>
  );
}

/** "Find symbol" dialog over held symbols plus the watchlist — replaces the
 * old wall of one button per symbol (design/screens/Main.dc.html). */
function FindSymbolDialog({
  open,
  symbols,
  held,
  selected,
  onPick,
  onClose,
}: {
  open: boolean;
  symbols: string[];
  held: Set<string>;
  selected: string | null;
  onPick: (symbol: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setQuery('');
  }
  const options = useMemo(() => {
    const q = query.trim().toUpperCase();
    return q ? symbols.filter((s) => s.includes(q)) : symbols;
  }, [symbols, query]);
  return (
    <Modal
      open={open}
      title="Find symbol"
      onClose={onClose}
      footer={
        <DialogButton variant="cancel" onClick={onClose}>
          Close
        </DialogButton>
      }
    >
      <input
        autoFocus
        type="search"
        aria-label="Find a symbol"
        placeholder="Type to filter"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        style={{
          height: 'var(--sa-input-height)',
          boxSizing: 'border-box',
          width: '100%',
          padding: '0 var(--sa-space-3)',
          borderRadius: 'var(--sa-radius-md)',
          border: '1px solid var(--sa-border-strong)',
          background: 'var(--sa-bg)',
          color: 'var(--sa-text)',
          fontSize: 'var(--sa-text-base)',
        }}
      />
      <ul
        role="listbox"
        aria-label="Symbols"
        style={{
          margin: 0,
          padding: 0,
          listStyle: 'none',
          maxHeight: '320px',
          overflowY: 'auto',
        }}
      >
        {options.map((symbol) => (
          <li key={symbol}>
            <button
              type="button"
              role="option"
              aria-selected={symbol === selected}
              onClick={() => onPick(symbol)}
              className="sa-row"
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                gap: 'var(--sa-space-3)',
                minHeight: 'var(--sa-control-height)',
                padding: '0 var(--sa-space-3)',
                border: 0,
                borderRadius: 'var(--sa-radius-sm)',
                background: 'transparent',
                color: 'var(--sa-text)',
                fontSize: 'var(--sa-text-base)',
                fontWeight: 600,
                cursor: 'pointer',
                textAlign: 'left',
              }}
            >
              {symbol}
              {held.has(symbol) && (
                <span
                  style={{
                    fontSize: 'var(--sa-text-sm)',
                    color: 'var(--sa-info)',
                    fontWeight: 400,
                  }}
                >
                  held
                </span>
              )}
            </button>
          </li>
        ))}
        {options.length === 0 && (
          <li
            style={{
              padding: 'var(--sa-space-4)',
              color: 'var(--sa-text-muted)',
              fontSize: 'var(--sa-text-md)',
            }}
          >
            No symbol in the universe matches “{query}”.
          </li>
        )}
      </ul>
    </Modal>
  );
}

export default function Dashboard() {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  const positionsRes = useApiResource(getPositions);
  const watchlistRes = useApiResource(getWatchlist);
  const approvalsRes = useApiResource(getApprovals);
  const budgetRes = useApiResource(getRiskBudget);

  // The charted symbol persists in the URL (?symbol=): an explicit param
  // wins, else the first held position, else the first watchlist row.
  const urlSymbol = useMemo(() => {
    const raw = searchParams.get('symbol');
    return raw ? raw.toUpperCase() : null;
  }, [searchParams]);
  const heldSymbols = useMemo(
    () => (positionsRes.data?.open ?? []).map((p) => p.symbol),
    [positionsRes.data],
  );
  const watchSymbols = useMemo(
    () => (watchlistRes.data?.rows ?? []).map((r) => r.symbol),
    [watchlistRes.data],
  );
  const chartSymbol = urlSymbol ?? heldSymbols[0] ?? watchSymbols[0] ?? null;
  const heldSet = useMemo(() => new Set(heldSymbols), [heldSymbols]);
  const pickerSymbols = useMemo(
    () => [...heldSymbols, ...watchSymbols.filter((s) => !heldSet.has(s))],
    [heldSymbols, watchSymbols, heldSet],
  );
  const pickSymbol = useCallback(
    (symbol: string) => setSearchParams({ symbol }),
    [setSearchParams],
  );
  useEffect(() => {
    if (chartSymbol && urlSymbol !== chartSymbol) {
      setSearchParams({ symbol: chartSymbol }, { replace: true });
    }
  }, [chartSymbol, urlSymbol, setSearchParams]);

  // The latest-decision card tracks the selected symbol.
  const dashboardRes = useApiResource(
    () => getDashboard(chartSymbol ?? undefined),
    [chartSymbol],
  );
  const chartRes = useApiResource(
    // reload() ignores the enabled flag, so the fetcher itself must not
    // hit /api/chart/ without a symbol.
    () => (chartSymbol === null ? Promise.resolve(null) : getChart(chartSymbol)),
    [chartSymbol],
    chartSymbol !== null,
  );

  useLiveUpdates(
    ['decisions', 'header', 'performance', 'reviews'],
    dashboardRes.reload,
  );
  useLiveUpdates(['watchlist'], watchlistRes.reload);
  useLiveUpdates(['approvals'], () => {
    approvalsRes.reload();
    dashboardRes.reload();
  });
  useLiveUpdates(['positions', 'orders'], () => {
    positionsRes.reload();
    dashboardRes.reload();
    chartRes.reload();
  });
  // The risk budget card reads the gate context: positions, orders and
  // account updates (header) all change its inputs.
  useLiveUpdates(['positions', 'orders', 'header'], budgetRes.reload);
  useLiveUpdates(['watchlist', 'decisions'], chartRes.reload);

  // Page-level state: two or more failed cards, or a request that never got
  // an answer, means the backend is probably unreachable — banner + 15 s
  // auto-retry (design/screens/States.dc.html).
  const resources = [
    dashboardRes,
    watchlistRes,
    approvalsRes,
    positionsRes,
    budgetRes,
    chartRes,
  ];
  const failures = resources
    .map((r) => r.failure)
    .filter((f): f is FetchFailure => f !== null);
  const backendDown =
    failures.some((f) => f.unreachable) || failures.length >= 2;
  const failedRef = useRef(resources.filter((r) => r.failure !== null));
  useEffect(() => {
    failedRef.current = resources.filter((r) => r.failure !== null);
  });
  useEffect(() => {
    if (!backendDown) return;
    const timer = setInterval(() => {
      failedRef.current.forEach((r) => r.reload());
    }, 15_000);
    return () => clearInterval(timer);
  }, [backendDown]);
  const retryFailed = useCallback(() => {
    failedRef.current.forEach((r) => r.reload());
  }, []);
  const failSince =
    failures.length > 0
      ? new Date(Math.min(...failures.map((f) => f.at.getTime())))
      : null;
  const okTimes = resources
    .map((r) => r.updatedAt)
    .filter((d): d is Date => d !== null);
  const lastOk =
    okTimes.length > 0
      ? new Date(Math.max(...okTimes.map((d) => d.getTime())))
      : null;

  const dashboard = dashboardRes.data;
  const chart = chartRes.data;
  const stats = dashboard?.stats;
  const passCount = watchlistRes.data
    ? watchlistRes.data.rows.filter((r) => r.rules.status === 'pass').length
    : null;
  const sincePct = stats?.since_start_pct ?? null;

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      {backendDown && (
        <Banner
          variant="negative"
          role="status"
          actions={
            <button
              type="button"
              onClick={retryFailed}
              style={{
                height: 36,
                padding: '0 var(--sa-space-3)',
                borderRadius: 'var(--sa-radius-md)',
                border: '1px solid var(--sa-negative-border)',
                background: 'transparent',
                color: 'var(--sa-negative-soft)',
                fontSize: 'var(--sa-text-md)',
                fontWeight: 500,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              Retry now
            </button>
          }
        >
          <b>
            Can&rsquo;t reach the Stock Trading Agent backend
            {failSince ? ` since ${displayTime(failSince.toISOString())}` : ''}.
          </b>{' '}
          {lastOk
            ? `Showing data from ${displayTime(lastOk.toISOString())}, marked stale. `
            : ''}
          Retrying every 15 seconds. Stops at Alpaca keep protecting positions.
        </Banner>
      )}

      {approvalsRes.failure && (approvalsRes.data?.items.length ?? 0) === 0 && (
        <ErrorBanner
          what="pending approvals"
          failure={approvalsRes.failure}
          updatedAt={approvalsRes.updatedAt}
          onRetry={approvalsRes.reload}
        />
      )}
      {approvalsRes.data && approvalsRes.data.items.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
          {approvalsRes.failure && (
            <ErrorBanner
              what="pending approvals"
              failure={approvalsRes.failure}
              updatedAt={approvalsRes.updatedAt}
              onRetry={approvalsRes.reload}
            />
          )}
          {approvalsRes.data.items.map((a) => (
            <ApprovalCard
              key={a.id}
              approval={a}
              killSwitchOn={approvalsRes.data?.kill_switch_on ?? false}
              paused={approvalsRes.data?.paused ?? false}
              nextCycle={approvalsRes.data?.next_cycle ?? null}
              onResolved={() => {
                approvalsRes.reload();
                dashboardRes.reload();
                positionsRes.reload();
              }}
            />
          ))}
        </div>
      )}

      {(dashboard === null ||
        dashboard.cycle_line !== null ||
        dashboard.cycle_counter !== null) && (
        <div
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
          <CardBody res={dashboardRes} what="the cycle state">
            {(d) => (
              <>
                {d.cycle_line && (
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
                      {d.cycle_line.label}
                    </span>
                    <span
                      style={{ flex: '1 1 420px', color: 'var(--sa-text-2)', lineHeight: 1.5 }}
                    >
                      {d.cycle_line.text}
                    </span>
                  </>
                )}
                {d.cycle_counter && <CycleCounter counter={d.cycle_counter} />}
              </>
            )}
          </CardBody>
        </div>
      )}

      {dashboardRes.failure && (
        <ErrorBanner
          what="the stats"
          failure={dashboardRes.failure}
          updatedAt={dashboardRes.updatedAt}
          onRetry={dashboardRes.reload}
        />
      )}
      {dashboard === null ? (
        dashboardRes.failure === null && (
          <div
            style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)' }}
            aria-hidden="true"
          >
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="sa-skel"
                style={{
                  flex: '1 1 190px',
                  height: 96,
                  borderRadius: 'var(--sa-radius-lg)',
                  background: 'var(--sa-panel)',
                }}
              />
            ))}
          </div>
        )
      ) : (
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--sa-space-3)',
          alignItems: 'stretch',
          opacity: dashboardRes.failure ? 0.55 : 1,
        }}
      >
        <Link
          to="/positions"
          className="sa-tile"
          aria-label="Positions, open Positions and orders"
          style={{ display: 'flex', flex: '1 1 190px', minWidth: 0 }}
        >
          <StatCard
            label="Positions"
            value={`${stats?.positions ?? 0} of ${stats?.positions_cap ?? '—'}`}
            sub={`cash $${stats?.cash ?? '—'} · no margin`}
            style={{ flex: '1 1 auto', width: '100%' }}
          />
        </Link>
        <Link
          to="/inspector?source=live"
          className="sa-tile"
          aria-label="Model calls, open the LLM inspector"
          title={
            stats
              ? `Decision calls in the shown session: at most ${stats.calls_per_cycle} per cycle, ${stats.cycles_today} cycles.` +
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
            label={
              dashboard.session.shown_is_today
                ? 'Model calls today'
                : `Model calls · ${sessionDayShort(dashboard.session.shown_date)}`
            }
            value={`${stats?.calls_today ?? 0}`}
            bar={{
              width: `${stats && stats.calls_max > 0 ? Math.min(100, (stats.calls_today / stats.calls_max) * 100) : 0}%`,
              color: 'var(--sa-info)',
            }}
            sub={`of ${stats?.calls_max ?? '—'} max · $${stats ? Number(stats.calls_cost_today).toFixed(3) : '—'} this session · $${stats ? Number(stats.calls_cost_month).toFixed(2) : '—'} this month`}
            style={{ flex: '1 1 auto', width: '100%', cursor: 'pointer' }}
          />
        </Link>
        <Link
          to="/performance"
          className="sa-tile"
          aria-label="Since start, open Performance"
          style={{ display: 'flex', flex: '1 1 190px', minWidth: 0 }}
        >
          <StatCard
            label="Since start"
            value={sincePct === null ? '—' : signedPct(sincePct)}
            valueColor={
              sincePct === null
                ? undefined
                : sincePct > 0
                  ? 'var(--sa-positive)'
                  : sincePct < 0
                    ? 'var(--sa-negative)'
                    : undefined
            }
            sub={`SPY ${stats?.spy_pct !== null && stats?.spy_pct !== undefined ? signedPct(stats.spy_pct) : '—'} · ${stats?.since_label ?? ''}`}
            style={{ flex: '1 1 auto', width: '100%' }}
          />
        </Link>
      </div>
      )}

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--sa-space-5)',
          alignItems: 'stretch',
        }}
      >
        <BriefingCard res={dashboardRes} />
        <RiskBudgetCard
          res={budgetRes}
          reentryBlocked={positionsRes.data?.today.reentry_blocked ?? []}
        />
      </div>

      {dashboard?.market_closed && (
        <MarketClosed
          mc={dashboard.market_closed}
          day={
            dashboard.session.shown_is_today
              ? undefined
              : dashboard.session.shown_date
          }
        />
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
          <Card>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 'var(--sa-space-3)',
                flexWrap: 'wrap',
              }}
            >
              <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>
                {chartSymbol ?? 'Chart'}
              </h2>
              {chart && (
                <>
                  <span
                    style={{
                      fontFamily: 'var(--sa-font-mono)',
                      fontSize: 'var(--sa-text-price)',
                      fontWeight: 500,
                    }}
                  >
                    ${formatPrice(chart.price)}
                  </span>
                  <span
                    style={{
                      fontFamily: 'var(--sa-font-mono)',
                      fontSize: 'var(--sa-text-md)',
                      color:
                        chart.day_change_pct !== null && chart.day_change_pct >= 0
                          ? 'var(--sa-positive)'
                          : 'var(--sa-negative-text)',
                    }}
                  >
                    {formatDayChange(chart.day_change_pct)} today
                  </span>
                  {chart.warming_up && <Chip variant="info">Warming up</Chip>}
                </>
              )}
              <span style={{ flex: 1 }} />
              <div
                role="group"
                aria-label="Pick a symbol"
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: '6px',
                  alignItems: 'center',
                }}
              >
                {heldSymbols.map((symbol) => (
                  <button
                    key={symbol}
                    type="button"
                    aria-pressed={symbol === chartSymbol}
                    onClick={() => pickSymbol(symbol)}
                    style={{
                      height: '36px',
                      padding: '0 var(--sa-space-3)',
                      borderRadius: 'var(--sa-radius-md)',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '6px',
                      fontSize: 'var(--sa-text-md)',
                      fontWeight: 600,
                      cursor: 'pointer',
                      border: `1px solid ${symbol === chartSymbol ? 'var(--sa-accent)' : 'var(--sa-border-strong)'}`,
                      background:
                        symbol === chartSymbol
                          ? 'var(--sa-accent-bg)'
                          : 'var(--sa-raised)',
                      color: 'var(--sa-text)',
                    }}
                  >
                    {symbol}
                    <span
                      style={{ fontSize: 'var(--sa-text-xs)', color: 'var(--sa-info)' }}
                    >
                      held
                    </span>
                  </button>
                ))}
                <button
                  type="button"
                  aria-haspopup="listbox"
                  onClick={() => setPickerOpen(true)}
                  style={{
                    height: '36px',
                    padding: '0 var(--sa-space-3)',
                    borderRadius: 'var(--sa-radius-md)',
                    border: '1px solid var(--sa-border-strong)',
                    background: 'var(--sa-bg)',
                    color: 'var(--sa-text-muted)',
                    fontSize: 'var(--sa-text-md)',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 'var(--sa-space-2)',
                    cursor: 'pointer',
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 14px' }}>
                    <circle cx="11" cy="11" r="6" />
                    <line x1="16" y1="16" x2="21" y2="21" />
                  </svg>
                  Find symbol
                </button>
              </div>
            </div>
            <div
              style={{
                fontSize: 'var(--sa-text-sm)',
                color: 'var(--sa-text-muted)',
                marginTop: '-4px',
              }}
            >
              1h bars · last 5 sessions · SIP, 15 min delayed · or pick a row in
              the watchlist
            </div>
            <CardBody
              res={chartRes}
              what={chartSymbol ? `the chart for ${chartSymbol}` : 'the chart'}
              isEmpty={(d) => d === null || d.bars.length === 0}
              skeleton={
                <div
                  className="sa-skel"
                  aria-hidden="true"
                  style={{
                    height: '340px',
                    borderRadius: 'var(--sa-radius-md)',
                    background: 'var(--sa-subtle)',
                  }}
                />
              }
              empty={
                <div style={{ height: '340px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--sa-text-muted)' }}>
                  {chartSymbol
                    ? `No bars loaded for ${chartSymbol} yet — bars appear after its first data pull.`
                    : 'Pick a symbol to chart.'}
                </div>
              }
            >
              {(d) => d !== null && <PriceChart bars={d.bars} levels={d.levels} />}
            </CardBody>
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
          <LatestDecision res={dashboardRes} />
        </div>
      </div>

      <Watchlist
        res={watchlistRes}
        selected={chartSymbol}
        onSelect={pickSymbol}
      />

      {/* Design: positions and the feed sit side by side, wrapping at
          narrow widths (Main.dc.html). */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--sa-space-5)',
          alignItems: 'stretch',
        }}
      >
        <div style={{ flex: '3 1 520px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <OpenPositionsMini
            res={positionsRes}
            nextCycle={dashboard?.session.next_cycle ?? null}
            nextCycleToday={dashboard?.cycle_counter?.next_cycle_today !== false}
            passCount={passCount}
          />
        </div>
        <div style={{ flex: '2 1 380px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <Feed res={dashboardRes} />
        </div>
      </div>

      <FindSymbolDialog
        open={pickerOpen}
        symbols={pickerSymbols}
        held={heldSet}
        selected={chartSymbol}
        onPick={(symbol) => {
          pickSymbol(symbol);
          setPickerOpen(false);
        }}
        onClose={() => setPickerOpen(false)}
      />
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
