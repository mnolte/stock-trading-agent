import { describe, expect, it } from 'vitest';

import { changeDirection, diffDirections, diffMap, diffSettings } from './settingsDiff';
import type { SettingsForm } from '../types/api';

function base(): SettingsForm {
  return {
    execution: {
      mode: 'automatic',
      entry_limit_offset_pct: 0.05,
      entry_timeout_minutes: 10,
      max_spread_pct: 0.15,
    },
    selection: {
      mode: 'rank',
      confidence_weight: 0.5,
      last_slot_min_ev_r: null,
    },
    rules: { rsi_min: 40, rsi_max: 70 },
    ask: {
      enabled: true,
      model: 'claude-sonnet-5-5',
      monthly_cost_cap_usd: 15,
      max_cost_per_answer_usd: 0.25,
      keep_days: 90,
      instructions: '',
    },
    decision: {
      model: 'gpt-4o-mini',
      temperature: 0,
      prompt: 'decision.v1',
      min_confidence_enter: 0.55,
      stop_atr_min: 1.5,
      stop_atr_max: 4,
      min_reward_risk: 1.5,
      max_calls_per_cycle: 10,
    },
    risk: {
      risk_per_trade_pct: 0.5,
      max_position_pct: 20,
      max_open_risk_pct: 2.5,
      max_open_positions: 4,
      max_per_sector: 2,
      daily_loss_limit_pct: 2,
      losing_streak: 3,
      shock_brake_1h_pct: 1.5,
      shock_brake_session_pct: 2.5,
      earnings_blackout_days: 3,
      hold_through_earnings: false,
      drawdown_kill_pct: 8,
      max_order_pct: 25,
      max_orders_per_cycle: 6,
    },
    performance: { slippage_pct_per_side: 0.05, starting_equity: 10000 },
    reviews: {
      briefing_enabled: true,
      monthly_cost_cap_usd: 2,
      models: {
        weekly: 'gpt-4o-mini',
        briefing: 'gpt-4o-mini',
        verdict: 'gpt-4o-mini',
      },
    },
    readiness: {
      min_closed_trades: 100,
      min_sessions: 60,
      max_drawdown_pct: 6,
      min_avg_r: 0.2,
      min_profit_factor: 1.3,
      ratio_min_trades: 30,
      min_proposal_pass_pct: 75,
      max_calibration_gap_pts: 10,
      min_cycles_completed_pct: 98,
      max_price_blocked_pct: 5,
      error_auto_resolve_minutes: 60,
    },
    auth: { idle_hours: 12 },
    schedule: { cycles: ['10:46', '11:46'], last_cycle_no_entries: false },
    notifications: {
      repeat_limit_minutes: 30,
      quiet_hours: { start: '23:00', end: '08:00' },
      events: {
        kill_switch: ['browser', 'email'],
        trade: [],
      },
    },
    universe: {
      symbols: ['SPY', 'AAPL'],
      sectors: { SPY: 'Index', AAPL: 'Technology' },
      allowed_sectors: ['Index', 'Technology'],
      mode: 'fixed',
      screen: {
        min_price: 10,
        min_avg_dollar_volume: 100_000_000,
        lookback_sessions: 20,
        drop_after_failed_screens: 3,
      },
    },
  };
}

describe('diffSettings', () => {
  it('is empty when nothing changed', () => {
    expect(diffSettings(base(), base())).toEqual([]);
  });

  it('reports changed values with their labels', () => {
    const edited = base();
    edited.risk.risk_per_trade_pct = 0.4;
    edited.execution.mode = 'supervised';
    const diffs = diffSettings(base(), edited);
    expect(diffs).toEqual([
      { label: 'Execution mode', from: 'automatic', to: 'supervised' },
      { label: 'Risk per trade', from: '0.5', to: '0.4' },
    ]);
  });

  it('reports new universe sectors', () => {
    const edited = base();
    edited.universe.symbols.push('XOM');
    edited.universe.sectors.XOM = 'Energy';
    const diffs = diffSettings(base(), edited);
    expect(diffs).toContainEqual({
      label: 'Sector XOM',
      from: '—',
      to: 'Energy',
    });
    expect(diffs).toContainEqual({
      label: 'Universe',
      from: 'SPY, AAPL',
      to: 'SPY, AAPL, XOM',
    });
  });

  it('reports the universe mode and screen thresholds as leaf diffs', () => {
    const edited = base();
    edited.universe.mode = 'screened';
    edited.universe.screen.min_price = 15;
    edited.universe.screen.min_avg_dollar_volume = 200_000_000;
    edited.universe.screen.lookback_sessions = 40;
    edited.universe.screen.drop_after_failed_screens = 5;
    const diffs = diffSettings(base(), edited);
    expect(diffs).toContainEqual({
      label: 'Universe mode',
      from: 'Fixed list',
      to: 'Screened pool',
    });
    expect(diffs).toContainEqual({
      label: 'Min price',
      from: '$10',
      to: '$15',
    });
    expect(diffs).toContainEqual({
      label: 'Min average dollar volume',
      from: '$100M',
      to: '$200M',
    });
    expect(diffs).toContainEqual({
      label: 'Average over',
      from: '20 sessions',
      to: '40 sessions',
    });
    expect(diffs).toContainEqual({
      label: 'Drop after',
      from: '3 days',
      to: '5 days',
    });
  });

  it('diffs a section the saved settings never had', () => {
    // A section added to the form after the saved tree (ask, PR 36):
    // diffing must not crash on the missing section.
    const current = base() as Partial<SettingsForm>;
    delete current.ask;
    const diffs = diffSettings(current as SettingsForm, base());
    expect(diffs).toContainEqual({
      label: 'Ask model',
      from: '—',
      to: 'claude-sonnet-5-5',
    });
    expect(diffMap(current as SettingsForm, base())['ask.model']).toBe('—');
  });

  it('reports review model changes as leaf diffs', () => {
    const edited = base();
    edited.reviews.models!.weekly = 'claude-sonnet-5-5';
    const diffs = diffSettings(base(), edited);
    expect(diffs).toContainEqual({
      label: 'Weekly review model',
      from: 'gpt-4o-mini',
      to: 'claude-sonnet-5-5',
    });
  });

  it('reports decision gate sub-keys as leaf diffs', () => {
    const edited = base();
    edited.decision.v5 = {
      trend_gate: false,
      trend_gate_regimes: ['up'],
      max_rr: 3,
      base_rate_sessions: 60,
      price_vs_signal_adverse_atr: 0.5,
    };
    edited.decision.v3 = { min_room_atr: 1.5, min_edge: 0.2 };
    const diffs = diffSettings(base(), edited);
    expect(diffs).toContainEqual({
      label: 'v5 trend gate',
      from: '—',
      to: 'off',
    });
    expect(diffs).toContainEqual({
      label: 'Regimes that pass the trend gate',
      from: '—',
      to: 'up',
    });
    expect(diffs).toContainEqual({
      label: 'v3 room to first level',
      from: '—',
      to: '1.5',
    });
    const map = diffMap(base(), edited);
    expect(map['decision.v5.trend_gate']).toBe('—');
    expect(map['decision.v5.max_rr']).toBe('—');
    expect(map['decision.v3.min_edge']).toBe('—');
  });

  it('reports changed values inside an existing gate group', () => {
    const current = base();
    current.decision.v5 = {
      trend_gate: true,
      trend_gate_regimes: ['up', 'sideways'],
      max_rr: 3,
    };
    const edited = base();
    edited.decision.v5 = {
      trend_gate: true,
      trend_gate_regimes: ['up'],
      max_rr: 4,
    };
    const diffs = diffSettings(current, edited);
    expect(diffs).toEqual([
      { label: 'Regimes that pass the trend gate', from: 'up, sideways', to: 'up' },
      { label: 'v5 max reward to risk', from: '3', to: '4' },
    ]);
  });

  it('reports notification leaves and channel lists', () => {
    const edited = base();
    edited.notifications.repeat_limit_minutes = 15;
    edited.notifications.quiet_hours.end = '07:30';
    edited.notifications.events.trade = ['browser'];
    const diffs = diffSettings(base(), edited);
    expect(diffs).toContainEqual({
      label: 'Repeat limit',
      from: '30 min',
      to: '15 min',
    });
    expect(diffs).toContainEqual({
      label: 'Quiet hours to',
      from: '08:00',
      to: '07:30',
    });
    expect(diffs).toContainEqual({
      label: 'Trade opened or closed · channels',
      from: 'banner only',
      to: 'browser',
    });
  });
});

// The backend's map (GET /api/settings → directions), item 082 — a
// representative slice; the backend test walks all of config.example.yaml.
const DIRS: Record<string, string> = {
  'risk.risk_per_trade_pct': 'up',
  'risk.max_open_positions': 'up',
  'risk.hold_through_earnings': 'on',
  'rules.require_daily_above_sma50': 'off',
  'rules.rsi_min': 'down',
  'rules.rsi_max': 'up',
  'decision.min_confidence_enter': 'down',
  'decision.model': 'neutral',
  'decision.v5.trend_gate': 'off',
  'decision.v5.trend_gate_regimes': 'longer',
  'execution.time_in_force': 'neutral',
  'universe.names': 'neutral',
};

describe('changeDirection (item 069, map from the API since 082)', () => {
  it('tags the kickoff examples', () => {
    // risk per trade down = tighter, up = looser
    expect(changeDirection('risk.risk_per_trade_pct', 0.6, 0.5, DIRS)).toBe('tighter');
    expect(changeDirection('risk.risk_per_trade_pct', 0.5, 0.6, DIRS)).toBe('looser');
    // max open positions up = looser
    expect(changeDirection('risk.max_open_positions', 4, 5, DIRS)).toBe('looser');
    // a narrower RSI range = tighter on both ends
    expect(changeDirection('rules.rsi_min', 40, 45, DIRS)).toBe('tighter');
    expect(changeDirection('rules.rsi_max', 70, 65, DIRS)).toBe('tighter');
    // trend gate off = looser
    expect(changeDirection('decision.v5.trend_gate', true, false, DIRS)).toBe('looser');
    // adding a regime to trend_gate_regimes = looser
    expect(
      changeDirection('decision.v5.trend_gate_regimes', ['up'], ['up', 'sideways'], DIRS),
    ).toBe('looser');
    expect(
      changeDirection('decision.v5.trend_gate_regimes', ['up', 'sideways'], ['up'], DIRS),
    ).toBe('tighter');
    // on / off kinds
    expect(changeDirection('risk.hold_through_earnings', false, true, DIRS)).toBe('looser');
    expect(changeDirection('rules.require_daily_above_sma50', true, false, DIRS)).toBe('looser');
    expect(changeDirection('rules.require_daily_above_sma50', false, true, DIRS)).toBe('tighter');
  });

  it('leaves neutral and unmapped settings untagged', () => {
    expect(changeDirection('decision.model', 'a', 'b', DIRS)).toBeNull();
    expect(changeDirection('execution.time_in_force', 'gtc', 'ioc', DIRS)).toBeNull();
    expect(changeDirection('performance.slippage_pct_per_side', 0.05, 0.1, DIRS)).toBeNull();
    expect(changeDirection('notifications.repeat_limit_minutes', 30, 60, DIRS)).toBeNull();
    // Ancestor entries cover nested leaves (universe.names.SPY).
    expect(changeDirection('universe.names.SPY', 'a', 'b', DIRS)).toBeNull();
  });

  it('diffDirections follows diffMap paths and the API map', () => {
    const current = base();
    current.decision.v5 = { trend_gate: true };
    const edited = base();
    edited.risk.risk_per_trade_pct = 0.6;
    edited.decision.v5 = { trend_gate: false };
    const dirs = diffDirections(current, edited, DIRS);
    expect(dirs['risk.risk_per_trade_pct']).toBe('looser');
    expect(dirs['decision.v5.trend_gate']).toBe('looser');
    expect(dirs['performance.slippage_pct_per_side']).toBeUndefined();
  });

  it('diffMap reports decision.v6 leaves', () => {
    const current = base();
    const edited = base();
    edited.decision.v6 = { sideways_max_target_atr: 3 };
    const map = diffMap(current, edited);
    expect(map['decision.v6.sideways_max_target_atr']).toBeDefined();
  });
});
