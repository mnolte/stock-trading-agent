import type { SettingsForm } from '../types/api';

// Client-side mirror of backend/app/settings_service.py EDITABLE: every
// editable field, its kind and its allowed range. Values are checked as
// they are typed; Save stays disabled while any rule fails (docs/SPEC.md,
// Settings screen).

export type FieldKind =
  | 'number'
  | 'int'
  | 'bool'
  | 'str'
  | 'mode'
  | 'universe_mode'
  | 'time';

export interface FieldRule {
  kind: FieldKind;
  min?: number;
  max?: number;
  label: string;
}

export const FIELD_RULES: Record<string, FieldRule> = {
  'execution.mode': { kind: 'mode', label: 'Execution mode' },
  'execution.entry_limit_offset_pct': { kind: 'number', min: 0, max: 10, label: 'Entry limit above ask' },
  'execution.entry_timeout_minutes': { kind: 'int', min: 1, max: 240, label: 'Cancel unfilled entry after' },
  'execution.max_spread_pct': { kind: 'number', min: 0, max: 10, label: 'Max spread' },
  'rules.rsi_min': { kind: 'number', min: 0, max: 100, label: 'RSI from' },
  'rules.rsi_max': { kind: 'number', min: 0, max: 100, label: 'RSI to' },
  'decision.model': { kind: 'str', label: 'Model' },
  'decision.temperature': { kind: 'number', min: 0, max: 2, label: 'Temperature' },
  'decision.prompt': { kind: 'str', label: 'Decision prompt' },
  'decision.min_confidence_enter': { kind: 'number', min: 0, max: 1, label: 'Min confidence to enter' },
  'decision.stop_atr_min': { kind: 'number', min: 0, max: 20, label: 'Stop distance from' },
  'decision.stop_atr_max': { kind: 'number', min: 0, max: 20, label: 'Stop distance to' },
  'decision.min_reward_risk': { kind: 'number', min: 0, max: 20, label: 'Min reward to risk' },
  'decision.max_calls_per_cycle': { kind: 'int', min: 1, max: 100, label: 'Max model calls per cycle' },
  'risk.risk_per_trade_pct': { kind: 'number', min: 0, max: 5, label: 'Risk per trade' },
  'risk.max_position_pct': { kind: 'number', min: 0, max: 100, label: 'Max position size' },
  'risk.max_open_risk_pct': { kind: 'number', min: 0, max: 100, label: 'Total open risk' },
  'risk.max_open_positions': { kind: 'int', min: 1, max: 50, label: 'Max open positions' },
  'risk.max_per_sector': { kind: 'int', min: 1, max: 20, label: 'Max positions per sector' },
  'risk.daily_loss_limit_pct': { kind: 'number', min: 0, max: 100, label: 'Daily loss limit' },
  'risk.losing_streak': { kind: 'int', min: 0, max: 20, label: 'Losing streak' },
  'risk.shock_brake_1h_pct': { kind: 'number', min: 0, max: 100, label: 'Shock brake, last 1h' },
  'risk.shock_brake_session_pct': { kind: 'number', min: 0, max: 100, label: 'Shock brake, since open' },
  'risk.earnings_blackout_days': { kind: 'int', min: 0, max: 30, label: 'Blackout before earnings' },
  'risk.hold_through_earnings': { kind: 'bool', label: 'Hold through earnings' },
  'risk.drawdown_kill_pct': { kind: 'number', min: 0, max: 100, label: 'Drawdown kill' },
  'risk.max_order_pct': { kind: 'number', min: 0, max: 100, label: 'Max single order' },
  'risk.max_orders_per_cycle': { kind: 'int', min: 1, max: 50, label: 'Max orders per cycle' },
  'performance.slippage_pct_per_side': { kind: 'number', min: 0, max: 10, label: 'Assumed slippage' },
  'reviews.briefing_enabled': { kind: 'bool', label: 'Daily briefing' },
  'reviews.monthly_cost_cap_usd': { kind: 'number', min: 0, max: 100, label: 'Review cost cap' },
  'auth.idle_hours': { kind: 'int', min: 1, max: 168, label: 'Sign out after inactivity' },
  'notifications.repeat_limit_minutes': { kind: 'int', min: 0, max: 1440, label: 'Repeat limit' },
  'notifications.quiet_hours.start': { kind: 'time', label: 'Quiet hours from' },
  'notifications.quiet_hours.end': { kind: 'time', label: 'Quiet hours to' },
  'universe.mode': { kind: 'universe_mode', label: 'Universe' },
  'universe.screen.min_price': { kind: 'number', min: 0, label: 'Min price' },
  'universe.screen.min_avg_dollar_volume': { kind: 'number', min: 0, label: 'Min average dollar volume' },
  'universe.screen.lookback_sessions': { kind: 'int', min: 1, max: 260, label: 'Average over' },
  'universe.screen.drop_after_failed_screens': { kind: 'int', min: 1, max: 20, label: 'Drop after' },
};

function fieldAt(form: SettingsForm, path: string): unknown {
  let cur: unknown = form;
  for (const part of path.split('.')) {
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

function validateField(value: unknown, rule: FieldRule): string | null {
  switch (rule.kind) {
    case 'number':
    case 'int': {
      const num = typeof value === 'number' ? value : Number(value);
      if (Number.isNaN(num)) return `${rule.label}: not a number`;
      if (rule.min !== undefined && num < rule.min)
        return `${rule.label}: must be at least ${rule.min}`;
      if (rule.max !== undefined && num > rule.max)
        return `${rule.label}: must be at most ${rule.max}`;
      if (rule.kind === 'int' && !Number.isInteger(num))
        return `${rule.label}: must be a whole number`;
      return null;
    }
    case 'mode':
      return value === 'automatic' || value === 'supervised'
        ? null
        : `${rule.label}: must be automatic or supervised`;
    case 'universe_mode':
      return value === 'fixed' || value === 'screened'
        ? null
        : `${rule.label}: must be fixed or screened`;
    case 'str':
      return typeof value === 'string' && value.trim()
        ? null
        : `${rule.label}: required`;
    case 'time': {
      if (typeof value !== 'string') return `${rule.label}: required`;
      const m = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
      if (!m || Number(m[1]) > 23 || Number(m[2]) > 59)
        return `${rule.label}: use HH:MM, e.g. 23:00`;
      return null;
    }
    default:
      return null;
  }
}

/** path -> error message for every invalid field, including the
 * cross-field orderings (RSI from < to, stop distance from < to). */
export function validateForm(form: SettingsForm): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const [path, rule] of Object.entries(FIELD_RULES)) {
    const message = validateField(fieldAt(form, path), rule);
    if (message) errors[path] = message;
  }
  if (
    !errors['rules.rsi_min'] &&
    !errors['rules.rsi_max'] &&
    Number(form.rules.rsi_min) >= Number(form.rules.rsi_max)
  ) {
    errors['rules.rsi_min'] = 'RSI from must be below RSI to';
    errors['rules.rsi_max'] = 'RSI from must be below RSI to';
  }
  if (
    !errors['decision.stop_atr_min'] &&
    !errors['decision.stop_atr_max'] &&
    Number(form.decision.stop_atr_min) >= Number(form.decision.stop_atr_max)
  ) {
    errors['decision.stop_atr_min'] = 'Stop distance from must be below stop distance to';
    errors['decision.stop_atr_max'] = 'Stop distance from must be below stop distance to';
  }
  return errors;
}
