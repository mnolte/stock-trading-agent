import { describe, expect, it } from 'vitest';

import { diffSettings } from './settingsDiff';
import type { SettingsForm } from '../types/api';

function base(): SettingsForm {
  return {
    execution: {
      mode: 'automatic',
      entry_limit_offset_pct: 0.05,
      entry_timeout_minutes: 10,
      max_spread_pct: 0.15,
    },
    rules: { rsi_min: 40, rsi_max: 70 },
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
    reviews: { briefing_enabled: true, monthly_cost_cap_usd: 2 },
    schedule: { cycles: ['10:46', '11:46'] },
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
