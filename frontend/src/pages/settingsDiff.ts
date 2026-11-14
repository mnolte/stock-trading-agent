import type { SettingsForm } from '../types/api';

export const FIELD_LABELS: Record<string, string> = {
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
  'decision.entry_gate': 'Entry gate',
  'decision.min_ev_r': 'Min expected R (ev gate)',
  'decision.v3.min_room_atr': 'v3 room to first level',
  'decision.v3.min_edge': 'v3 min edge over break-even',
  'decision.v4.near_level_atr': 'v4 near-a-level distance',
  'decision.v5.trend_gate': 'v5 trend gate',
  'decision.v5.trend_gate_regimes': 'Regimes that pass the trend gate',
  'decision.v5.max_rr': 'v5 max reward to risk',
  'decision.v5.base_rate_sessions': 'v5 base rate window',
  'decision.v5.price_vs_signal_adverse_atr': 'v5 adverse price move',
  'decision.v6.sideways_max_target_atr': 'v6 sideways, high volatility: target at most',
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
  'ask.enabled': 'Ask the agent',
  'ask.model': 'Ask model',
  'ask.monthly_cost_cap_usd': 'Ask monthly cap',
  'ask.max_cost_per_answer_usd': 'Ask max cost per answer',
  'ask.keep_days': 'Ask chats kept for',
  'ask.instructions': 'Ask standing instructions',
  'schedule.cycles': 'Cycle times',
  'schedule.last_cycle_no_entries': 'No new entries in the last cycle',
  'notifications.repeat_limit_minutes': 'Repeat limit',
  'notifications.quiet_hours.start': 'Quiet hours from',
  'notifications.quiet_hours.end': 'Quiet hours to',
  'events.fomc.enabled': 'FOMC window',
  'events.fomc.block_minutes_before': 'FOMC window blocks from',
  'events.fomc.block_minutes_after': 'FOMC window blocks until',
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
  if (value === undefined || value === null) return '—';
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

/** Nested leaves of a decision version gate (`decision.v3`/`v4`/`v5`),
 * as `decision.<ver>.<sub>` paths. */
function decisionGateLeafRows(
  key: 'v3' | 'v4' | 'v5' | 'v6',
  current: SettingsForm,
  edited: SettingsForm,
  map: Record<string, string> | null,
  rows: DiffRow[] | null,
): void {
  dictLeafRows('decision', key, current, edited, fmtVal,
    (sub) => FIELD_LABELS[`decision.${key}.${sub}`] ?? `Decision ${key} ${sub}`,
    map, rows);
}

/** Nested leaves of `universe.screen`, as `universe.screen.<key>` paths. */
function screenLeafRows(
  current: SettingsForm,
  edited: SettingsForm,
  map: Record<string, string> | null,
  rows: DiffRow[] | null,
): void {
  const cur = (current.universe?.screen ?? {}) as Record<string, unknown>;
  const next = (edited.universe?.screen ?? {}) as Record<string, unknown>;
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

/** Nested leaves of `events.fomc`, as `events.fomc.<sub>` paths. */
function fomcLeafRows(
  current: SettingsForm,
  edited: SettingsForm,
  map: Record<string, string> | null,
  rows: DiffRow[] | null,
): void {
  dictLeafRows('events', 'fomc', current, edited, fmtVal,
    (sub) => FIELD_LABELS[`events.fomc.${sub}`] ?? `FOMC ${sub}`,
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
      if (path === 'decision.v3' || path === 'decision.v4' || path === 'decision.v5' || path === 'decision.v6') {
        decisionGateLeafRows(key as 'v3' | 'v4' | 'v5' | 'v6', current, edited, null, rows);
        continue;
      }
      if (path === 'universe.sectors') {
        const cur = (current.universe?.sectors ?? {}) as Record<string, string>;
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
      if (path === 'events.fomc') {
        fomcLeafRows(current, edited, null, rows);
        continue;
      }
      if (path === 'reviews.prompts') {
        reviewPromptLeafRows(current, edited, null, rows);
        continue;
      }
      if (path === 'universe.allowed_sectors') continue;
      const old = (current[section] as Record<string, unknown> | undefined)?.[key];
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
      if (path === 'decision.v3' || path === 'decision.v4' || path === 'decision.v5' || path === 'decision.v6') {
        decisionGateLeafRows(key as 'v3' | 'v4' | 'v5' | 'v6', current, edited, map, null);
        continue;
      }
      if (path === 'universe.sectors') {
        const cur = (current.universe?.sectors ?? {}) as Record<string, string>;
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
      if (path === 'events.fomc') {
        fomcLeafRows(current, edited, map, null);
        continue;
      }
      if (path === 'reviews.prompts') {
        reviewPromptLeafRows(current, edited, map, null);
        continue;
      }
      if (path === 'universe.allowed_sectors') continue;
      const old = (current[section] as Record<string, unknown> | undefined)?.[key];
      if (fmtVal(old) !== fmtVal(value)) map[path] = fmtPath(path, old);
    }
  }
  return map;
}

// ---------------------------------------------------------------------------
// Tighter / looser (item 069, widened by 082): the save bar tags every
// change with the direction it moves the agent. The map itself comes
// from the backend — GET /api/settings returns `directions`, the one
// map that also decides which saves need "Confirm it is you".

export type ChangeDirection = 'tighter' | 'looser';

/** The backend's direction kinds (settings_service.DIRECTIONS). */
export type SettingDirection =
  | 'up'
  | 'down'
  | 'on'
  | 'off'
  | 'wider'
  | 'longer'
  | 'shorter'
  | 'neutral';

export function settingLeaf(tree: unknown, path: string): unknown {
  let cur = tree;
  for (const part of path.split('.')) {
    if (cur === null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

/** The direction entry for a dotted leaf path: exact match first, then
 * the nearest covering ancestor — mirrors the backend's lookup. */
export function directionFor(
  path: string,
  directions: Record<string, string>,
): SettingDirection | null {
  let node = path;
  while (node) {
    const found = directions[node];
    if (found !== undefined) return found as SettingDirection;
    const cut = node.lastIndexOf('.');
    node = cut < 0 ? '' : node.slice(0, cut);
  }
  return null;
}

/** Total order for up/down compares: null sorts lowest, numbers
 * numerically, everything else as text (HH:MM times compare right). */
function orderKey(value: unknown): [number, number | string] {
  if (value === null || value === undefined) return [0, 0];
  if (typeof value === 'boolean') return [1, value ? 1 : 0];
  if (typeof value === 'number') return [1, value];
  if (typeof value === 'string' && value !== '') {
    const n = Number(value);
    if (!Number.isNaN(n)) return [1, n];
  }
  return [2, String(value)];
}

function keyLess(a: [number, number | string], b: [number, number | string]): boolean {
  return a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);
}

function asSet(value: unknown): Set<unknown> | null {
  if (!Array.isArray(value)) return null;
  return new Set(value);
}

function rangePair(value: unknown): [unknown, unknown] | null {
  if (Array.isArray(value) && value.length === 2) {
    return [value[0], value[1]];
  }
  if (value !== null && typeof value === 'object') {
    const rec = value as Record<string, unknown>;
    if ('min' in rec && 'max' in rec) return [rec.min, rec.max];
  }
  return null;
}

/** Direction of one changed leaf, or null when the setting is neutral
 * or outside the backend's map (anything the map doesn't cover). */
export function changeDirection(
  path: string,
  from: unknown,
  to: unknown,
  directions: Record<string, string>,
): ChangeDirection | null {
  const kind = directionFor(path, directions);
  if (kind === null || kind === 'neutral') return null;
  if (kind === 'on' || kind === 'off') {
    const was = Boolean(from);
    const is = Boolean(to);
    if (was === is) return null;
    return is === (kind === 'on') ? 'looser' : 'tighter';
  }
  if (kind === 'up' || kind === 'down') {
    const a = orderKey(from);
    const b = orderKey(to);
    if (a[0] === b[0] && a[1] === b[1]) return null;
    const rose = keyLess(a, b);
    return rose === (kind === 'up') ? 'looser' : 'tighter';
  }
  if (kind === 'longer' || kind === 'shorter') {
    const a = asSet(from);
    const b = asSet(to);
    if (a === null || b === null) return null;
    const added = [...b].some((v) => !a.has(v));
    const removed = [...a].some((v) => !b.has(v));
    const grew = added && !removed;
    const shrank = removed && !added;
    if (kind === 'longer') {
      if (grew) return 'looser';
      if (shrank) return 'tighter';
    } else {
      if (shrank) return 'looser';
      if (grew) return 'tighter';
    }
    return null;
  }
  if (kind === 'wider') {
    const a = rangePair(from);
    const b = rangePair(to);
    if (a === null || b === null) return null;
    const loWidened = keyLess(orderKey(b[0]), orderKey(a[0]));
    const hiWidened = keyLess(orderKey(a[1]), orderKey(b[1]));
    const loNarrowed = keyLess(orderKey(a[0]), orderKey(b[0]));
    const hiNarrowed = keyLess(orderKey(b[1]), orderKey(a[1]));
    const widened = loWidened || hiWidened;
    const narrowed = loNarrowed || hiNarrowed;
    if (widened && !narrowed) return 'looser';
    if (narrowed && !widened) return 'tighter';
    return null;
  }
  return null;
}

/** path -> direction for every changed leaf (keys of diffMap). */
export function diffDirections(
  current: SettingsForm,
  edited: SettingsForm,
  directions: Record<string, string>,
): Record<string, ChangeDirection> {
  const out: Record<string, ChangeDirection> = {};
  for (const path of Object.keys(diffMap(current, edited))) {
    // diffMap also emits synthetic universe.sector.* paths — they have
    // no entry in the backend's map and stay untagged.
    const dir = changeDirection(
      path,
      settingLeaf(current, path),
      settingLeaf(edited, path),
      directions,
    );
    if (dir !== null) out[path] = dir;
  }
  return out;
}

/** Verb for the looser-change confirmation: "Raises risk per trade …". */
export function changeVerb(
  path: string,
  from: unknown,
  to: unknown,
  directions: Record<string, string>,
): string | null {
  const kind = directionFor(path, directions);
  if (kind === null || kind === 'neutral') return null;
  if (kind === 'on' || kind === 'off') {
    const on = Boolean(to);
    return on ? 'Turns on' : 'Turns off';
  }
  if (kind === 'up' || kind === 'down') {
    const a = orderKey(from);
    const b = orderKey(to);
    if (a[0] === b[0] && a[1] === b[1]) return null;
    return keyLess(a, b) ? 'Raises' : 'Lowers';
  }
  if (kind === 'longer' || kind === 'shorter') {
    const a = asSet(from);
    const b = asSet(to);
    if (a === null || b === null) return null;
    const added = [...b].some((v) => !a.has(v));
    const removed = [...a].some((v) => !b.has(v));
    if (added && !removed) return 'Adds';
    if (removed && !added) return 'Removes';
    return null;
  }
  if (kind === 'wider') {
    const a = rangePair(from);
    const b = rangePair(to);
    if (a === null || b === null) return null;
    const widened =
      keyLess(orderKey(b[0]), orderKey(a[0])) ||
      keyLess(orderKey(a[1]), orderKey(b[1]));
    const narrowed =
      keyLess(orderKey(a[0]), orderKey(b[0])) ||
      keyLess(orderKey(b[1]), orderKey(a[1]));
    if (widened && !narrowed) return 'Widens';
    if (narrowed && !widened) return 'Narrows';
    return null;
  }
  return null;
}
