import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import type { WatchlistRow } from '../types/api';
import { Card } from './Card';
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
  rulesColor,
  rulesLabel,
} from './watchlistFormat';

const GRID = '90px 110px 90px minmax(200px, 1.3fr) minmax(150px, 1fr) 90px';

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
      role="tab"
      aria-selected={active}
      onClick={onClick}
      style={{
        height: '34px',
        padding: '0 var(--sa-space-3)',
        border: 0,
        borderRadius: 'var(--sa-radius-sm)',
        background: active ? 'var(--sa-subtle)' : 'transparent',
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
  rows,
  selected,
  onSelect,
  universeMode = 'fixed',
  watchlistRows = 15,
  earningsBlackoutDays = 3,
}: {
  rows: WatchlistRow[];
  selected: string | null;
  onSelect: (symbol: string) => void;
  universeMode?: 'fixed' | 'screened';
  watchlistRows?: number;
  earningsBlackoutDays?: number;
}) {
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
  const truncated = screened && !showAll && visible.length > watchlistRows;
  const shown = truncated ? visible.slice(0, watchlistRows) : visible;

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
      right={
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          {rows.length} symbols · <Link to="/universe">Universe</Link> · click a row
          to chart it
        </span>
      }
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)', alignItems: 'center' }}>
        <div
          role="tablist"
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
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          With a screened pool: sorted by rank, {watchlistRows} rows, then Show all
        </span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <div role="table" aria-label="Watchlist" style={{ minWidth: '760px' }}>
          <div
            role="row"
            style={{
              display: 'grid',
              gridTemplateColumns: GRID,
              gap: 'var(--sa-space-2)',
              padding: '0 10px var(--sa-space-2)',
              fontSize: 'var(--sa-text-sm)',
              color: 'var(--sa-text-muted)',
              borderBottom: '1px solid var(--sa-border)',
            }}
          >
            <span role="columnheader">Symbol</span>
            <span role="columnheader" style={{ textAlign: 'right' }}>Price</span>
            <span role="columnheader" style={{ textAlign: 'right' }}>Day</span>
            <span role="columnheader">Rules</span>
            <span role="columnheader">Last action</span>
            <span
              role="columnheader"
              title="Next earnings date. No new entries within 3 trading days."
              style={{ textDecoration: 'underline dotted', textUnderlineOffset: '3px', cursor: 'help' }}
            >
              Earnings
            </span>
          </div>
          {shown.map((row) => {
            const on = row.symbol === selected;
            const change = row.day_change_pct === null ? null : Number(row.day_change_pct);
            return (
              <button
                key={row.symbol}
                type="button"
                role="row"
                aria-pressed={on}
                onClick={() => onSelect(row.symbol)}
                style={{
                  width: '100%',
                  display: 'grid',
                  gridTemplateColumns: GRID,
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
                <span style={{ fontSize: 'var(--sa-text-md)', color: rulesColor(row) }}>
                  {rulesLabel(row)}
                </span>
                <span
                  style={{
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-sm)',
                    color: actionColor(row.last_action),
                  }}
                >
                  {formatLastAction(row.last_action, row.last_action_time)}
                </span>
                <span
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
      {truncated && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          style={{
            height: '34px',
            padding: '0 var(--sa-space-3)',
            borderRadius: 'var(--sa-radius-sm)',
            border: '1px solid var(--sa-border)',
            background: 'var(--sa-raised)',
            color: 'var(--sa-text-2)',
            fontSize: 'var(--sa-text-md)',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          Show all ({visible.length - watchlistRows} more)
        </button>
      )}
      {screened && showAll && visible.length > watchlistRows && (
        <button
          type="button"
          onClick={() => setShowAll(false)}
          style={{
            height: '34px',
            padding: '0 var(--sa-space-3)',
            borderRadius: 'var(--sa-radius-sm)',
            border: '1px solid var(--sa-border)',
            background: 'var(--sa-raised)',
            color: 'var(--sa-text-2)',
            fontSize: 'var(--sa-text-md)',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          Show fewer
        </button>
      )}
    </Card>
  );
}
