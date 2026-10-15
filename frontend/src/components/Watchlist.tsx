import type { WatchlistRow } from '../types/api';
import { Card } from './Card';
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

export function Watchlist({
  rows,
  selected,
  onSelect,
}: {
  rows: WatchlistRow[];
  selected: string | null;
  onSelect: (symbol: string) => void;
}) {
  return (
    <Card
      label="Watchlist"
      right={
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          {rows.length} symbols · click a row to chart it
        </span>
      }
    >
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
          {rows.map((row) => {
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
    </Card>
  );
}
