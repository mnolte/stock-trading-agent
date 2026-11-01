import type { SettingsForm } from '../types/api';

const FIELD_LABELS: Record<string, string> = {
  'execution.mode': 'Execution mode',
  'execution.entry_limit_offset_pct': 'Entry limit above ask',
  'execution.entry_timeout_minutes': 'Cancel unfilled entry after',
  'execution.max_spread_pct': 'Max spread',
  'rules.rsi_min': 'RSI from',
  'rules.rsi_max': 'RSI to',
  'decision.model': 'Model',
  'decision.temperature': 'Temperature',
  'decision.prompt': 'Prompt version',
  'decision.min_confidence_enter': 'Min confidence to enter',
  'decision.stop_atr_min': 'Stop distance from',
  'decision.stop_atr_max': 'Stop distance to',
  'decision.min_reward_risk': 'Min reward to risk',
  'decision.max_calls_per_cycle': 'Max model calls per cycle',
  'risk.risk_per_trade_pct': 'Risk per trade',
  'risk.max_position_pct': 'Max position size',
  'risk.max_open_risk_pct': 'Total open risk',
  'risk.max_open_positions': 'Max open positions',
  'risk.max_per_sector': 'Max positions per sector',
  'risk.daily_loss_limit_pct': 'Daily loss limit',
  'risk.losing_streak': 'Losing streak',
  'risk.shock_brake_1h_pct': 'Shock brake, last 1h',
  'risk.shock_brake_session_pct': 'Shock brake, since open',
  'risk.earnings_blackout_days': 'Blackout before earnings',
  'risk.hold_through_earnings': 'Hold through earnings',
  'risk.drawdown_kill_pct': 'Drawdown kill',
  'risk.max_order_pct': 'Max single order',
  'risk.max_orders_per_cycle': 'Max orders per cycle',
  'performance.slippage_pct_per_side': 'Assumed slippage',
  'reviews.briefing_enabled': 'Daily briefing',
  'reviews.monthly_cost_cap_usd': 'Review cost cap',
  'reviews.models.weekly': 'Weekly review model',
  'reviews.models.briefing': 'Daily briefing model',
  'reviews.models.verdict': 'Verdict model',
  'reviews.prompts.weekly': 'Weekly review prompt',
  'reviews.prompts.briefing': 'Daily briefing prompt',
  'reviews.prompts.verdict': 'Verdict prompt',
  'schedule.cycles': 'Cycle times',
  'notifications.repeat_limit_minutes': 'Repeat limit',
  'notifications.quiet_hours.start': 'Quiet hours from',
  'notifications.quiet_hours.end': 'Quiet hours to',
  'universe.symbols': 'Universe',
  'universe.mode': 'Universe mode',
  'universe.screen.min_price': 'Min price',
  'universe.screen.min_avg_dollar_volume': 'Min average dollar volume',
  'universe.screen.lookback_sessions': 'Average over',
  'universe.screen.drop_after_failed_screens': 'Drop after',
  'readiness.min_closed_trades': 'Readiness closed trades',
  'readiness.min_sessions': 'Readiness paper sessions',
  'readiness.max_drawdown_pct': 'Readiness max drawdown',
  'readiness.min_avg_r': 'Readiness average R',
  'readiness.min_profit_factor': 'Readiness profit factor',
  'readiness.ratio_min_trades': 'Readiness minimum trades for ratios',
  'readiness.min_proposal_pass_pct': 'Readiness proposals passing',
  'readiness.max_calibration_gap_pts': 'Readiness calibration gap',
  'readiness.min_cycles_completed_pct': 'Readiness cycles completed',
  'readiness.max_price_blocked_pct': 'Readiness price checks blocked',
  'readiness.error_auto_resolve_minutes': 'Errors auto-resolve after',
};

export interface DiffRow {
  label: string;
  from: string;
  to: string;
}

function fmtVal(value: unknown): string {
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') return value ? 'on' : 'off';
  return String(value);
}

/** Display value for a leaf path — money fields show units. */
export function fmtPath(path: string, value: unknown): string {
  if (path === 'universe.screen.min_avg_dollar_volume') {
    const n = Number(value);
    return Number.isNaN(n) ? String(value) : `$${n / 1_000_000}M`;
  }
  if (path === 'universe.screen.min_price') return `$${String(value)}`;
  if (path === 'universe.screen.lookback_sessions') return `${String(value)} sessions`;
  if (path === 'universe.screen.drop_after_failed_screens') return `${String(value)} days`;
  if (path === 'universe.mode') return value === 'screened' ? 'Screened pool' : 'Fixed list';
  if (path === 'notifications.repeat_limit_minutes') return `${String(value)} min`;
  return fmtVal(value);
}

// Event labels for `notifications.events.<key>` diff rows — mirrors
// backend/app/notifications/events.py EVENT_DEFS.
const EVENT_LABELS: Record<string, string> = {
  readiness_changed: 'Readiness changed',
  kill_switch: 'Kill switch turned on',
  missing_stop: 'Position without a stop at Alpaca',
  reconciliation: 'Reconciliation mismatch or adopted position',
  order_rejected: 'Order rejected by Alpaca',
  daily_loss_limit: 'Daily loss limit reached',
  shock_brake: 'Shock brake on',
  source_failing: 'Data source or API key failing',
  position_checks_failing: 'Position checks failing',
  model_failing: 'Model failing, or review or shadow cost cap reached',
  approval_waiting: 'Entry waiting for approval',
  exit_deferred_or_earnings_exit: 'Earnings exit tomorrow',
  trade: 'Trade opened or closed',
  daily_briefing: 'Daily briefing',
  weekly_review: 'Weekly review ready',
  verdicts_waiting: 'Verdicts waiting to confirm',
};

/** Channel list display for an events leaf: 'browser, email' or 'banner only'. */
function fmtChannels(value: unknown): string {
  if (Array.isArray(value) && value.length > 0) {
    return [...value].sort().join(' + ');
  }
  return 'banner only';
}

/** Nested leaves of a dict field (`notifications.quiet_hours`,
 * `notifications.events`), as `<section>.<key>.<sub>` paths. */
function dictLeafRows(
  section: keyof SettingsForm,
  key: string,
  current: SettingsForm,
  edited: SettingsForm,
  fmt: (value: unknown) => string,
  labelFor: (sub: string) => string,
  map: Record<string, string> | null,
  rows: DiffRow[] | null,
): void {
  const cur =
    ((current[section] as Record<string, unknown>)?.[key] as
      | Record<string, unknown>
      | undefined) ?? {};
  const next =
    ((edited[section] as Record<string, unknown>)?.[key] as
      | Record<string, unknown>
      | undefined) ?? {};
  for (const sub of new Set([...Object.keys(cur), ...Object.keys(next)])) {
    const path = `${section}.${key}.${sub}`;
    if (fmt(cur[sub]) === fmt(next[sub])) continue;
    if (map !== null) map[path] = fmt(cur[sub]);
    if (rows !== null) {
      rows.push({ label: labelFor(sub), from: fmt(cur[sub]), to: fmt(next[sub]) });
    }
  }
}

function notificationLeafRows(
  key: 'quiet_hours' | 'events',
  current: SettingsForm,
  edited: SettingsForm,
  map: Record<string, string> | null,
  rows: DiffRow[] | null,
): void {
  if (key === 'quiet_hours') {
    dictLeafRows('notifications', 'quiet_hours', current, edited, fmtVal,
      (sub) => FIELD_LABELS[`notifications.quiet_hours.${sub}`] ?? `Quiet hours ${sub}`,
      map, rows);
  } else {
    dictLeafRows('notifications', 'events', current, edited, fmtChannels,
      (sub) => `${EVENT_LABELS[sub] ?? sub} · channels`,
      map, rows);
  }
}

/** Nested leaves of `universe.screen`, as `universe.screen.<key>` paths. */
function screenLeafRows(
  current: SettingsForm,
  edited: SettingsForm,
  map: Record<string, string> | null,
  rows: DiffRow[] | null,
): void {
  const cur = current.universe.screen as Record<string, unknown>;
  const next = edited.universe.screen as Record<string, unknown>;
  for (const key of new Set([...Object.keys(cur), ...Object.keys(next)])) {
    const path = `universe.screen.${key}`;
    if (fmtVal(cur[key]) === fmtVal(next[key])) continue;
    if (map !== null) map[path] = fmtPath(path, cur[key]);
    if (rows !== null) {
      rows.push({
        label: FIELD_LABELS[path] ?? path,
        from: fmtPath(path, cur[key]),
        to: fmtPath(path, next[key]),
      });
    }
  }
}

/** Nested leaves of `reviews.models`, as `reviews.models.<task>` paths. */
function reviewModelLeafRows(
  current: SettingsForm,
  edited: SettingsForm,
  map: Record<string, string> | null,
  rows: DiffRow[] | null,
): void {
  dictLeafRows('reviews', 'models', current, edited, fmtVal,
    (sub) => FIELD_LABELS[`reviews.models.${sub}`] ?? `Review model ${sub}`,
    map, rows);
}

/** Nested leaves of `reviews.prompts`, as `reviews.prompts.<task>` paths. */
function reviewPromptLeafRows(
  current: SettingsForm,
  edited: SettingsForm,
  map: Record<string, string> | null,
  rows: DiffRow[] | null,
): void {
  dictLeafRows('reviews', 'prompts', current, edited, fmtVal,
    (sub) => FIELD_LABELS[`reviews.prompts.${sub}`] ?? `Review prompt ${sub}`,
    map, rows);
}

/** Client-side diff between the saved settings and the edited form. */
export function diffSettings(
  current: SettingsForm,
  edited: SettingsForm,
): DiffRow[] {
  const rows: DiffRow[] = [];
  for (const [section, body] of Object.entries(edited) as [
    keyof SettingsForm,
    object,
  ][]) {
    for (const [key, value] of Object.entries(body as Record<string, unknown>)) {
      const path = `${section}.${key}`;
      if (path === 'universe.sectors') {
        const cur = (current.universe.sectors ?? {}) as Record<string, string>;
        const next = value as Record<string, string>;
        for (const sym of new Set([...Object.keys(cur), ...Object.keys(next)])) {
          if (cur[sym] !== next[sym]) {
            rows.push({
              label: `Sector ${sym}`,
              from: cur[sym] ?? '—',
              to: next[sym] ?? '—',
            });
          }
        }
        continue;
      }
      if (path === 'universe.screen') {
        screenLeafRows(current, edited, null, rows);
        continue;
      }
      if (path === 'notifications.quiet_hours' || path === 'notifications.events') {
        notificationLeafRows(key as 'quiet_hours' | 'events', current, edited, null, rows);
        continue;
      }
      if (path === 'reviews.models') {
        reviewModelLeafRows(current, edited, null, rows);
        continue;
      }
      if (path === 'reviews.prompts') {
        reviewPromptLeafRows(current, edited, null, rows);
        continue;
      }
      if (path === 'universe.allowed_sectors') continue;
      const old = (current[section] as Record<string, unknown>)[key];
      if (fmtVal(old) !== fmtVal(value)) {
        rows.push({ label: FIELD_LABELS[path] ?? path, from: fmtPath(path, old), to: fmtPath(path, value) });
      }
    }
  }
  return rows;
}

/** Same walk as diffSettings, keyed by field path so a changed row can
 * show "Changed from …" and Reset in place. */
export function diffMap(
  current: SettingsForm,
  edited: SettingsForm,
): Record<string, string> {
  const map: Record<string, string> = {};
  for (const [section, body] of Object.entries(edited) as [
    keyof SettingsForm,
    object,
  ][]) {
    for (const [key, value] of Object.entries(body as Record<string, unknown>)) {
      const path = `${section}.${key}`;
      if (path === 'universe.sectors') {
        const cur = (current.universe.sectors ?? {}) as Record<string, string>;
        const next = value as Record<string, string>;
        for (const sym of new Set([...Object.keys(cur), ...Object.keys(next)])) {
          if (cur[sym] !== next[sym]) map[`universe.sector.${sym}`] = cur[sym] ?? '—';
        }
        continue;
      }
      if (path === 'universe.screen') {
        screenLeafRows(current, edited, map, null);
        continue;
      }
      if (path === 'notifications.quiet_hours' || path === 'notifications.events') {
        notificationLeafRows(key as 'quiet_hours' | 'events', current, edited, map, null);
        continue;
      }
      if (path === 'reviews.models') {
        reviewModelLeafRows(current, edited, map, null);
        continue;
      }
      if (path === 'reviews.prompts') {
        reviewPromptLeafRows(current, edited, map, null);
        continue;
      }
      if (path === 'universe.allowed_sectors') continue;
      const old = (current[section] as Record<string, unknown>)[key];
      if (fmtVal(old) !== fmtVal(value)) map[path] = fmtPath(path, old);
    }
  }
  return map;
}
