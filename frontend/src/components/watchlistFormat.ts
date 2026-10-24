import type { WatchlistRow } from '../types/api';

export function rulesLabel(row: WatchlistRow): string {
  if (row.warming_up) return 'Warming up';
  if (row.rules.status === 'pass') return 'Passed';
  if (row.rules.status === 'held') return 'Held · reviewed each cycle';
  return row.rules.reason ?? 'Failed';
}

export function rulesColor(row: WatchlistRow): string {
  if (row.warming_up) return 'var(--sa-info)';
  if (row.rules.status === 'pass') return 'var(--sa-positive)';
  if (row.rules.status === 'held') return 'var(--sa-info)';
  return 'var(--sa-text-muted)';
}

export function formatPrice(price: string | null): string {
  if (price === null) return '—';
  const n = Number(price);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function formatDayChange(pct: number | null): string {
  if (pct === null || Number.isNaN(pct)) return '—';
  return `${pct >= 0 ? '+' : '−'}${Math.abs(pct).toFixed(2)}%`;
}

export function formatEarnings(isoDate: string | null): string {
  if (!isoDate) return '—';
  const d = new Date(`${isoDate}T00:00:00Z`);
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
}

export function formatLastAction(action: string | null, isoTime: string | null): string {
  if (!action) return '—';
  const label = action.replace(/_/g, ' ').toUpperCase();
  if (!isoTime) return label;
  const time = new Date(isoTime).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Amsterdam',
  });
  return `${label} · ${time}`;
}

export function actionColor(action: string | null): string {
  switch (action) {
    case 'enter':
      return 'var(--sa-positive)';
    case 'skip':
      return 'var(--sa-accent)';
    case 'exit':
      return 'var(--sa-negative-text)';
    case 'tighten_stop':
      return 'var(--sa-info)';
    case 'hold':
      return 'var(--sa-text-2)';
    default:
      return 'var(--sa-text-disabled)';
  }
}
