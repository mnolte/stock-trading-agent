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
  'schedule.cycles': 'Cycle times',
  'universe.symbols': 'Universe',
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
      if (path === 'universe.allowed_sectors') continue;
      const old = (current[section] as Record<string, unknown>)[key];
      if (fmtVal(old) !== fmtVal(value)) {
        rows.push({ label: FIELD_LABELS[path] ?? path, from: fmtVal(old), to: fmtVal(value) });
      }
    }
  }
  return rows;
}
