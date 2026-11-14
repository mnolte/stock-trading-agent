import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import type { Resource } from '../api/useResource';
import type { WatchlistResponse } from '../types/api';
import { Card } from './Card';
import { CardBody } from './cardState';
import { SkeletonTable } from './skeletons';
import { formatAmsTime, formatNyTitle } from './decisionFormat';
import {
  filterCounts,
  filterRows,
  searchRows,
  sortByRank,
  type WatchlistFilter,
} from './watchlistFilters';
import {
  actionColor,
  formatDayChange,
  formatEarnings,
  formatLastAction,
  formatPrice,
  formatSector,
  rulesColor,
  rulesLabel,
} from './watchlistFormat';

const GRID = '90px 110px 90px 98px minmax(200px, 1.3fr) minmax(150px, 1fr) 90px';
// Screened mode adds a leading rank column (docs/SPEC.md: the rank shows
// on the Universe screen and the dashboard).
const GRID_RANKED = `44px ${GRID}`;

function Tab({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      style={{
        height: '34px',
        padding: '0 var(--sa-space-3)',
        border: 0,
        borderRadius: 'var(--sa-radius-sm)',
        background: active ? 'var(--sa-border-strong)' : 'transparent',
        color: active ? 'var(--sa-text)' : 'var(--sa-text-muted)',
        fontSize: 'var(--sa-text-md)',
        fontWeight: 600,
        cursor: 'pointer',
      }}
    >
      {label}{' '}
      <span style={{ fontFamily: 'var(--sa-font-mono)', fontWeight: 400 }}>
        {count}
      </span>
    </button>
  );
}

export function Watchlist({
  res,
  selected,
  onSelect,
}: {
  res: Resource<WatchlistResponse>;
  selected: string | null;
  onSelect: (symbol: string) => void;
}) {
  const rows = useMemo(() => res.data?.rows ?? [], [res.data]);
  const universeMode = res.data?.universe_mode ?? 'fixed';
  const watchlistRows = res.data?.watchlist_rows ?? 15;
  const earningsBlackoutDays = res.data?.earnings_blackout_days ?? 3;
  const screened = universeMode === 'screened';
  const [filter, setFilter] = useState<WatchlistFilter>('all');
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);

  const counts = useMemo(
    () => filterCounts(rows, earningsBlackoutDays),
    [rows, earningsBlackoutDays],
  );
  const visible = useMemo(() => {
    const filtered = filterRows(rows, filter, earningsBlackoutDays);
    const searched = searchRows(filtered, query);
    return screened ? sortByRank(searched) : searched;
  }, [rows, filter, query, screened, earningsBlackoutDays]);
  // The 15-row cut never hides a held symbol (design: "held symbols
  // always listed") — held rows past the cut stay in, in rank order.
  const hiddenNonHeld = visible
    .slice(watchlistRows)
    .filter((r) => r.rules.status !== 'held').length;
  const truncated = screened && !showAll && hiddenNonHeld > 0;
  const shown = truncated
    ? visible.filter((r, i) => i < watchlistRows || r.rules.status === 'held')
    : visible;

  const tabs: { key: WatchlistFilter; label: string; count: number }[] = [
    { key: 'all', label: 'All', count: counts.all },
    { key: 'pass', label: 'Pass rules', count: counts.pass },
    { key: 'held', label: 'Held', count: counts.held },
    {
      key: 'earnings',
      label: `Earnings ≤ ${earningsBlackoutDays} days`,
      count: counts.earnings,
    },
  ];

  return (
    <Card
      label="Watchlist"
      ariaLabel="Watchlist"
      busy={res.loading}
      refreshing={res.refreshing}
      style={{ gap: '14px' }}
      right={
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          {screened
            ? `screened pool · ${rows.length} eligible · `
            : `${rows.length} symbols · `}
          <Link to="/universe">Universe</Link> · click a row
          to chart it
        </span>
      }
    >
      <CardBody
        res={res}
        what="the watchlist"
        skeleton={
          <SkeletonTable
            columns={[
              { label: 'Symbol' },
              { label: 'Price', align: 'right' },
              { label: 'Day', align: 'right' },
              { label: 'Sector' },
              { label: 'Rules' },
              { label: 'Last action' },
              { label: 'Earnings' },
            ]}
            rows={8}
            track={GRID}
          />
        }
        isEmpty={(d) => d.rows.length === 0}
        empty={
          <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)' }}>
            No symbols on the watchlist yet — the next screen pass fills it
            from the pool.
          </p>
        }
      >
        {() => (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center' }}>
        <div
          role="group"
          aria-label="Filter watchlist"
          style={{
            display: 'inline-flex',
            flexWrap: 'wrap',
            padding: '3px',
            background: 'var(--sa-raised)',
            border: '1px solid var(--sa-border)',
            borderRadius: 'var(--sa-radius-md)',
          }}
        >
          {tabs.map((tab) => (
            <Tab
              key={tab.key}
              label={tab.label}
              count={tab.count}
              active={filter === tab.key}
              onClick={() => setFilter(tab.key)}
            />
          ))}
        </div>
        <input
          type="search"
          aria-label="Find a symbol"
          placeholder="Find a symbol"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            height: 'var(--sa-input-height)',
            boxSizing: 'border-box',
            flex: '1 1 160px',
            maxWidth: '240px',
            padding: '0 var(--sa-space-3)',
            borderRadius: 'var(--sa-radius-md)',
            border: '1px solid var(--sa-border-strong)',
            background: 'var(--sa-bg)',
            color: 'var(--sa-text)',
            fontSize: 'var(--sa-text-base)',
          }}
        />
        <span style={{ flex: 1 }} />
      </div>
      <div style={{ overflowX: 'auto', minWidth: 0 }}>
        {/* Item 073: on a phone the whole header hides and each row is two
            lines — symbol/price/day, then rules with the last action. */}
        <div role="list" aria-label="Watchlist" className="sa-wl" style={{ minWidth: '890px' }}>
          <div
            aria-hidden="true"
            className="sa-hide-phone"
            style={{
              display: 'grid',
              gridTemplateColumns: screened ? GRID_RANKED : GRID,
              gap: 'var(--sa-space-2)',
              padding: '0 10px var(--sa-space-2)',
              fontSize: 'var(--sa-text-sm)',
              color: 'var(--sa-text-muted)',
              borderBottom: '1px solid var(--sa-border)',
            }}
          >
            {screened && (
              <span
                role="columnheader"
                title="Place in today's liquidity screen: return over 20 sessions minus SPY's. Model calls go in rank order."
                style={{ textDecoration: 'underline dotted', textUnderlineOffset: '3px', cursor: 'help' }}
              >
                Rank
              </span>
            )}
            <span role="columnheader">Symbol</span>
            <span role="columnheader" style={{ textAlign: 'right' }}>Price</span>
            <span role="columnheader" style={{ textAlign: 'right' }}>Day</span>
            <span
              role="columnheader"
              title="The symbol's sector ETF and that sector's rank of 11 by strength vs SPY at the last cycle (item 068). Index funds have none."
              style={{ textDecoration: 'underline dotted', textUnderlineOffset: '3px', cursor: 'help', paddingLeft: '14px' }}
            >
              Sector
            </span>
            <span role="columnheader">Rules</span>
            <span role="columnheader">Last action</span>
            <span
              role="columnheader"
              title={`Next earnings date. No new entries within ${earningsBlackoutDays} trading days.`}
              style={{ textDecoration: 'underline dotted', textUnderlineOffset: '3px', cursor: 'help' }}
            >
              Earnings
            </span>
          </div>
          {shown.map((row) => {
            const on = row.symbol === selected;
            const change = row.day_change_pct;
            return (
              <button
                key={row.symbol}
                type="button"
                className="sa-row sa-wl-row"
                aria-pressed={on}
                aria-label={`${row.symbol}, chart it`}
                onClick={() => onSelect(row.symbol)}
                style={{
                  width: '100%',
                  display: 'grid',
                  gridTemplateColumns: screened ? GRID_RANKED : GRID,
                  gap: 'var(--sa-space-2)',
                  alignItems: 'center',
                  minHeight: 'var(--sa-control-height)',
                  padding: '0 10px',
                  border: 0,
                  borderBottom: '1px solid var(--sa-border)',
                  textAlign: 'left',
                  fontSize: 'var(--sa-text-base)',
                  color: 'var(--sa-text)',
                  cursor: 'pointer',
                  background: on ? 'var(--sa-accent-bg)' : 'transparent',
                  boxShadow: on ? 'inset 3px 0 0 var(--sa-accent)' : 'none',
                }}
              >
                {screened && (
                  <span
                    className="sa-hide-phone"
                    style={{
                      fontFamily: 'var(--sa-font-mono)',
                      fontSize: 'var(--sa-text-sm)',
                      color: 'var(--sa-text-muted)',
                    }}
                  >
                    {row.rank ?? '—'}
                  </span>
                )}
                <span style={{ fontWeight: 600 }}>{row.symbol}</span>
                <span style={{ fontFamily: 'var(--sa-font-mono)', textAlign: 'right' }}>
                  {formatPrice(row.price)}
                </span>
                <span
                  style={{
                    fontFamily: 'var(--sa-font-mono)',
                    textAlign: 'right',
                    color:
                      change === null
                        ? 'var(--sa-text-muted)'
                        : change >= 0
                          ? 'var(--sa-positive)'
                          : 'var(--sa-negative-text)',
                  }}
                >
                  {formatDayChange(row.day_change_pct)}
                </span>
                <span
                  className="sa-hide-phone"
                  style={{
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-sm)',
                    color: 'var(--sa-text-2)',
                    paddingLeft: '14px',
                  }}
                >
                  {formatSector(row)}
                </span>
                <span className="sa-wl-rules" style={{ fontSize: 'var(--sa-text-md)', color: rulesColor(row) }}>
                  {rulesLabel(row)}
                  {row.last_action && (
                    <span
                      className="sa-wl-act"
                      title={row.last_action_time ? formatNyTitle(row.last_action_time) : undefined}
                      style={{
                        fontFamily: 'var(--sa-font-mono)',
                        fontSize: 'var(--sa-text-sm)',
                        color: actionColor(row.last_action),
                      }}
                    >
                      {' '}· {row.last_action.replace(/_/g, ' ').toUpperCase()}
                      {row.last_action_time ? ` · ${formatAmsTime(row.last_action_time)}` : ''}
                    </span>
                  )}
                </span>
                <span
                  className="sa-hide-phone"
                  title={row.last_action_time ? formatNyTitle(row.last_action_time) : undefined}
                  style={{
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-sm)',
                    color: actionColor(row.last_action),
                  }}
                >
                  {formatLastAction(row.last_action, row.last_action_time)}
                </span>
                <span
                  className="sa-hide-phone"
                  style={{
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-sm)',
                    color: 'var(--sa-text-2)',
                  }}
                >
                  {formatEarnings(row.earnings_date)}
                </span>
              </button>
            );
          })}
        </div>
      </div>
      {(screened || truncated || (screened && showAll && visible.length > watchlistRows)) && (
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: '8px',
            paddingTop: '10px',
          }}
        >
          {screened && (
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              Sorted by rank · {Math.min(watchlistRows, visible.length)} of {visible.length} eligible shown · held symbols always listed
              <span className="sa-phone-inline"> · turn the phone sideways for sector, last action and earnings</span>
            </span>
          )}
          <span style={{ display: 'flex', gap: '8px' }}>
            {truncated && (
              <button
                type="button"
                onClick={() => setShowAll(true)}
                style={{
                  height: '36px',
                  padding: '0 14px',
                  borderRadius: 'var(--sa-radius-sm)',
                  border: '1px solid var(--sa-border-strong)',
                  background: 'var(--sa-raised)',
                  color: 'var(--sa-text)',
                  fontSize: 'var(--sa-text-md)',
                  fontWeight: 500,
                  cursor: 'pointer',
                }}
              >
                Show all {visible.length}
              </button>
            )}
            {screened && showAll && visible.length > watchlistRows && (
              <button
                type="button"
                onClick={() => setShowAll(false)}
                style={{
                  height: '36px',
                  padding: '0 14px',
                  borderRadius: 'var(--sa-radius-sm)',
                  border: '1px solid var(--sa-border-strong)',
                  background: 'var(--sa-raised)',
                  color: 'var(--sa-text)',
                  fontSize: 'var(--sa-text-md)',
                  fontWeight: 500,
                  cursor: 'pointer',
                }}
              >
                Show fewer
              </button>
            )}
          </span>
        </div>
      )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
