import { formatPrice } from './watchlistFormat';

/** The stop-to-target bar shared by the dashboard position rows and the
 * Positions page (design: stop red, entry tick, white last-price dot,
 * target green). Prices that fall outside the stop–target span (a stop
 * above the entry, or the last price beyond the target) still place
 * correctly: the bar spans min(stop, entry) to max(target, last). */
export function StopTargetBar({
  stop,
  entry,
  last,
  target,
  labels = true,
}: {
  stop: number | null;
  entry: number;
  last: number | null;
  target: number | null;
  /** Show the price caption row under the bar (Positions page). */
  labels?: boolean;
}) {
  const lo = Math.min(stop ?? entry, entry);
  const hi = Math.max(target ?? last ?? entry, last ?? entry);
  const span = hi - lo;
  const pct = (v: number) =>
    span > 0 ? Math.min(100, Math.max(0, ((v - lo) / span) * 100)) : 0;
  const entryPct = pct(entry);
  const lastPct = last !== null ? pct(last) : null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
      <div aria-hidden="true" style={{ position: 'relative', width: '100%', height: 22 }}>
        <div style={{ position: 'absolute', left: 0, right: 0, top: 9, height: 4, borderRadius: 2, background: 'var(--sa-border)' }} />
        {lastPct !== null && (
          <div
            style={{
              position: 'absolute',
              left: `${entryPct}%`,
              width: `${Math.max(0, lastPct - entryPct)}%`,
              top: 9,
              height: 4,
              borderRadius: 2,
              background: 'var(--sa-positive)',
            }}
          />
        )}
        {stop !== null && (
          <div title="Stop" style={{ position: 'absolute', left: `calc(${pct(stop)}% - 1px)`, top: 3, width: 2, height: 16, background: 'var(--sa-negative-text)' }} />
        )}
        <div title="Entry" style={{ position: 'absolute', left: `calc(${entryPct}% - 1px)`, top: 5, width: 2, height: 12, background: 'var(--sa-text-muted)' }} />
        {target !== null && (
          <div title="Target" style={{ position: 'absolute', left: `calc(${pct(target)}% - 1px)`, top: 3, width: 2, height: 16, background: 'var(--sa-positive)' }} />
        )}
        {lastPct !== null && (
          <div title="Last price" style={{ position: 'absolute', left: `calc(${lastPct}% - 5px)`, top: 6, width: 10, height: 10, borderRadius: '50%', background: 'var(--sa-text)', boxShadow: '0 0 0 2px var(--sa-panel)' }} />
        )}
      </div>
      {labels && (
        <span style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-xs)', color: 'var(--sa-text-muted)' }}>
          <span style={{ color: 'var(--sa-negative-text)' }}>
            stop {formatPrice(stop !== null ? String(stop) : null)}
          </span>
          <span>entry {formatPrice(String(entry))}{last !== null ? ` · last ${formatPrice(String(last))}` : ''}</span>
          <span style={{ color: 'var(--sa-positive)' }}>
            target {formatPrice(target !== null ? String(target) : null)}
          </span>
        </span>
      )}
    </div>
  );
}
