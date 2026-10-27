import type { HeaderState } from '../types/api';

/** Page name per route — same wording as the design screen titles. */
export function pageTitle(pathname: string): string {
  if (pathname.startsWith('/positions')) return 'Positions & orders';
  if (pathname.startsWith('/performance')) return 'Performance';
  if (pathname.startsWith('/inspector')) return 'LLM inspector';
  if (pathname.startsWith('/reviews')) return 'Reviews';
  if (pathname.startsWith('/strategies')) return 'Strategies';
  if (pathname.startsWith('/backtest')) return 'Backtest';
  if (pathname.startsWith('/decisions')) return 'Decision detail';
  if (pathname.startsWith('/readiness')) return 'Readiness';
  if (pathname.startsWith('/universe')) return 'Universe';
  if (pathname.startsWith('/settings')) return 'Settings';
  if (pathname.startsWith('/states')) return 'States & dialogs';
  if (pathname.startsWith('/logs')) return 'Logs';
  if (pathname.startsWith('/dev/components')) return 'Components';
  return 'Dashboard';
}

/** Browser tab: "Stock Trading Agent — Dashboard" (design) plus the
 * agent's state when it is not the normal running one (docs/SPEC.md,
 * Interaction and states — open, paused, kill switch). */
export function documentTitle(
  pathname: string,
  state: HeaderState | null,
): string {
  const agent = state?.kill_switch
    ? 'Kill switch'
    : state?.paused
      ? 'Paused'
      : state?.session.state === 'open'
        ? 'Open'
        : null;
  return (
    `Stock Trading Agent — ${pageTitle(pathname)}` + (agent ? ` · ${agent}` : '')
  );
}
