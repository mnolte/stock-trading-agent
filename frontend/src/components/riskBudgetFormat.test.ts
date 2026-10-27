import { describe, expect, it } from 'vitest';

import {
  budgetHints,
  drawdownUsed,
  pct2,
  reEntryText,
  riskPerTradeText,
  roomText,
  sectorsText,
  shockBrakeText,
  signedPct,
  trimDecimal,
} from './riskBudgetFormat';
import type { BudgetMeter, RiskBudgetResponse } from '../types/api';

const meter = (over: Partial<BudgetMeter>): BudgetMeter => ({
  used_pct: '0',
  limit_pct: '2',
  level: 'normal',
  fill_pct: '0',
  ...over,
});

const budget = (over: Partial<RiskBudgetResponse>): RiskBudgetResponse => ({
  settings_version: 3,
  open_risk: { ...meter({ used_pct: '0.34', limit_pct: '2.5', fill_pct: '13.6' }), used_usd: '34' },
  daily_loss: { ...meter({ used_pct: '0' }), session_pnl_pct: '0' },
  drawdown: meter({ used_pct: '0.28', limit_pct: '8', fill_pct: '3.5' }),
  room: { trades: 2, limited_by: 'positions' },
  sectors: [
    { sector: 'Technology', used: 1, cap: 2 },
    { sector: 'Financials', used: 1, cap: 2 },
  ],
  risk_per_trade_pct: '0.5',
  losing_streak: { count: 0, max: 3, factor: '1' },
  shock_brake: {
    on: false,
    limit_1h_pct: '1.5',
    limit_session_pct: '2.5',
    spy_1h_pct: '0.12',
    spy_session_pct: '0.38',
  },
  re_entry: { allowed: false },
  positions: { open: 2, cap: 4 },
  ...over,
});

describe('trimDecimal', () => {
  it('trims trailing zeros from Decimal strings', () => {
    expect(trimDecimal('2.50')).toBe('2.5');
    expect(trimDecimal('2.0')).toBe('2');
    expect(trimDecimal('0.5')).toBe('0.5');
  });
  it('shows a dash for missing values', () => {
    expect(trimDecimal(null)).toBe('—');
    expect(trimDecimal(undefined)).toBe('—');
  });
});

describe('pct2 / signedPct', () => {
  it('formats two-decimal and signed percents', () => {
    expect(pct2('0.34')).toBe('0.34%');
    expect(pct2(null)).toBe('—');
    expect(signedPct('0.12')).toBe('+0.12%');
    expect(signedPct('-0.62')).toBe('−0.62%');
    expect(signedPct(null)).toBe('—');
  });
});

describe('drawdownUsed', () => {
  it('prefixes the design minus when below the peak', () => {
    expect(drawdownUsed(meter({ used_pct: '0.28' }))).toBe('−0.28%');
  });
  it('shows 0.00% at the peak', () => {
    expect(drawdownUsed(meter({ used_pct: '0' }))).toBe('0.00%');
  });
});

describe('roomText', () => {
  it('names the limiting constraint', () => {
    expect(roomText({ trades: 2, limited_by: 'positions' })).toBe('2 more trades');
    expect(roomText({ trades: 1, limited_by: 'cash' })).toBe('1 more trade · limited by cash');
    expect(roomText({ trades: 0, limited_by: 'open_risk' })).toBe(
      '0 more trades · limited by open risk',
    );
  });
});

describe('fact rows', () => {
  it('lists sector use against the cap', () => {
    expect(sectorsText(budget({}).sectors)).toBe('Technology 1/2 · Financials 1/2');
    expect(sectorsText([])).toBe('—');
  });
  it('shows risk per trade with the losing streak', () => {
    expect(riskPerTradeText(budget({}))).toBe('0.5% · losing streak 0 of 3');
    expect(
      riskPerTradeText(
        budget({
          risk_per_trade_pct: '0.25',
          losing_streak: { count: 3, max: 3, factor: '0.5' },
        }),
      ),
    ).toBe('0.25% · losing streak 3 of 3');
  });
  it('shows shock brake state with SPY moves', () => {
    expect(shockBrakeText(budget({}))).toBe('off · SPY +0.12% last 1h, +0.38% since open');
    expect(
      shockBrakeText(
        budget({
          shock_brake: {
            on: true,
            limit_1h_pct: '1.5',
            limit_session_pct: '2.5',
            spy_1h_pct: '-1.7',
            spy_session_pct: null,
          },
        }),
      ),
    ).toBe('on · SPY −1.70% last 1h');
    expect(
      shockBrakeText(
        budget({
          shock_brake: {
            on: false,
            limit_1h_pct: '1.5',
            limit_session_pct: '2.5',
            spy_1h_pct: null,
            spy_session_pct: null,
          },
        }),
      ),
    ).toBe('off');
  });
  it('lists symbols blocked from re-entry this session', () => {
    expect(reEntryText(budget({}), [])).toBe('none today');
    expect(reEntryText(budget({}), ['MSFT'])).toBe('MSFT');
    expect(reEntryText(budget({}), ['JPM', 'MSFT'])).toBe('JPM · MSFT');
    expect(reEntryText(budget({ re_entry: { allowed: true } }), ['MSFT'])).toBe(
      'allowed by settings',
    );
  });
});

describe('hover explanations', () => {
  it('interpolates the configured limits', () => {
    expect(budgetHints.dailyLoss('2')).toContain('At −2% no new entries');
    expect(budgetHints.drawdown('8')).toContain('At −8% the kill switch');
    expect(budgetHints.sectors(2)).toBe('At most 2 positions per sector; working entries count.');
    expect(budgetHints.shockBrake('1.5', '2.5')).toBe(
      'No new entries this session when SPY drops 1.5% in a 1h bar or 2.5% since the open.',
    );
    expect(budgetHints.reEntry).toBe(
      'A symbol sold or stopped out today is not bought again until the next session.',
    );
    expect(budgetHints.riskPerTrade({ max: 3, factor: '0.5' })).toBe(
      'After 3 losses in a row risk per trade is halved until a win.',
    );
  });
});
