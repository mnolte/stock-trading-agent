import { describe, expect, it } from 'vitest';

import {
  checkTurnedAdverse,
  earningsChipLabel,
  firstTouchLabel,
  gateRows,
  orderStatusText,
  signalRows,
  sipBarEnd,
} from './decisionDetail';
import type { DecisionDetail } from '../types/api';

it.each([
  ['trend_1h', 'up', 'mixed', true],
  ['trend_daily', 'mixed', 'down', true],
  ['trend_1h', 'up', 'unavailable', false],
  ['regime_trend', 'up', '—', false],
  ['momentum', 'strong', 'unavailable', false],
  ['momentum', 'mixed', 'weak', true],
  ['momentum', 'strong', 'mixed', true],
  ['momentum', 'weak', 'strong', false],
] as const)('compares %s from %s to %s without treating unknowns as adverse', (name, before, current, adverse) => {
  expect(checkTurnedAdverse(name, before, current)).toBe(adverse);
});

const detail = (over: Partial<DecisionDetail>): DecisionDetail =>
  ({
    id: 1,
    cycle_time: '2026-10-15T14:46:00+00:00',
    symbol: 'MSFT',
    context: 'candidate',
    final_action: 'enter',
    no_call_reason: null,
    input_snapshot: {},
    rules_result: null,
    model_output: null,
    validation_result: null,
    gates_result: null,
    trade: null,
    outcome: null,
    next_outcome_due: null,
    earnings: null,
    execution_mode: 'automatic',
    eur_usd_rate: null,
    prompt_version: 'decision.v1',
    prompt_hash: 'abc',
    prompt_text: null,
    model: 'gpt-test',
    tokens_in: null,
    tokens_out: null,
    tokens: null,
    cost: null,
    latency_ms: null,
    raw_response: null,
    orders: [],
    approval: null,
    ...over,
  }) as DecisionDetail;

describe('gateRows', () => {
  it('maps code checks to pass/fail rows', () => {
    const rows = gateRows(
      detail({
        validation_result: {
          valid: false,
          model_action: 'enter',
          final_action: 'skip',
          changed_by_code: true,
          error: null,
          note: '',
          checks: [
            { name: 'Reward to risk ≥ 1.5', passed: false, detail: 'rr', value: '1.3' },
            { name: 'Confidence ≥ 0.55', passed: true, detail: '', value: '0.64' },
          ],
        },
      }),
    );
    expect(rows[0]).toEqual({ name: 'Reward to risk ≥ 1.5', detail: '1.3 · rr', state: 'fail' });
    expect(rows[1]).toEqual({ name: 'Confidence ≥ 0.55', detail: '0.64', state: 'pass' });
  });

  it.each(['Explanation check', 'Claim checks', 'Confidence sanity', 'Conflicts resolved'])(
    'distinguishes the non-blocking %s warning and keeps its detail', (name) => {
      for (const passed of [true, false]) {
        const decision = detail({});
        decision.validation_result = {
          valid: true, model_action: 'hold', final_action: 'hold', changed_by_code: false,
          error: null, note: '', checks: [{ name, passed, value: '0.85', detail: 'anchor 0.15' }],
        };
        expect(gateRows(decision)[0]).toEqual({
          name, detail: '0.85 · anchor 0.15', state: passed ? 'pass' : 'warning',
        });
      }
    },
  );

  it('marks sizing-limited gates as reduced, not plain pass', () => {
    const rows = gateRows(
      detail({
        gates_result: {
          risk_factor: '1',
          sizing: { shares: 4, limited_by: ['max_position'] },
          checks: [
            { name: 'max_position', passed: true, detail: '4 sh · 16.5%' },
            { name: 'cash_only', passed: true, detail: 'ok' },
          ],
        },
      }),
    );
    expect(rows[0].state).toBe('reduced');
    expect(rows[1].state).toBe('pass');
  });

  it('marks a halved losing-streak risk budget as reduced', () => {
    const rows = gateRows(
      detail({
        gates_result: {
          risk_factor: '0.5',
          checks: [
            { name: 'losing_streak', passed: true, detail: '3 losses in a row; risk ×0.5' },
          ],
        },
      }),
    );
    expect(rows[0].state).toBe('reduced');
  });

  it('humanizes gate names', () => {
    const rows = gateRows(
      detail({
        gates_result: {
          checks: [{ name: 'max_position', passed: true, detail: '4 sh' }],
        },
      }),
    );
    expect(rows[0].name).toBe('max position');
  });

  it('renders the re-entry gate as "Not traded today" with the symbol', () => {
    const rows = gateRows(
      detail({
        gates_result: {
          checks: [{ name: 'reentry', passed: true, detail: 'not traded today' }],
        },
      }),
    );
    expect(rows[0]).toEqual({ name: 'Not traded today', detail: 'MSFT', state: 'pass' });
  });

  it('keeps the reason when re-entry blocks an entry', () => {
    const rows = gateRows(
      detail({
        gates_result: {
          checks: [
            { name: 'reentry', passed: false, detail: 'sold earlier this session' },
          ],
        },
      }),
    );
    expect(rows[0]).toEqual({
      name: 'Not traded today',
      detail: 'sold earlier this session',
      state: 'fail',
    });
  });
});

describe('signalRows', () => {
  it('builds 1h and daily columns from the stored snapshot', () => {
    const rows = signalRows({
      price: 418.2,
      indicators_1h: {
        ema20: 417,
        ema50: 414.4,
        rsi14: 57,
        atr14: 3.1,
        vwap: 417.4,
        obv_trend: 'up',
        rs_vs_spy_pct: 0.9,
      },
      indicators_daily: {
        close: 416,
        sma50: 402.3,
        sma200: 379.6,
        atr_percentile: 52,
      },
    });
    const byName = Object.fromEntries(rows.map((r) => [r[0], r]));
    expect(byName['RSI 14'][1]).toBe('57');
    expect(byName['EMA 20 vs 50'][1]).toBe('+0.63%');
    expect(byName['vs session VWAP'][1]).toBe('+0.19%');
    expect(byName['vs SMA 50 / 200'][2]).toBe('+3.41% / +9.59%');
    expect(byName['OBV trend (20)'][1]).toBe('up');
    expect(byName['RS vs SPY (20)'][1]).toBe('+0.90%');
    expect(byName['ATR % · rank'][1]).toBe('0.74%');
    expect(byName['ATR % · rank'][2]).toBe('p52');
  });

  it('shows dashes for unavailable values', () => {
    const rows = signalRows({ indicators_1h: {}, indicators_daily: {} });
    const byName = Object.fromEntries(rows.map((r) => [r[0], r]));
    expect(byName['RSI 14'][1]).toBe('—');
    expect(byName['vs SMA 50 / 200'][2]).toBe('— / —');
    expect(byName['ATR % · rank'][2]).toBe('—');
  });
});

describe('sipBarEnd', () => {
  it('floors the decision time to the :30 session grid in New York', () => {
    // 16:46 Amsterdam = 10:46 New York (October, EDT vs CEST).
    expect(sipBarEnd('2026-10-15T14:46:00+00:00')).toBe('10:30');
    // Position checks at :01/:16 still report the last closed 1h bar,
    // which ended at the previous hour's :30 — not a :00 boundary.
    expect(sipBarEnd('2026-10-15T15:01:00+00:00')).toBe('10:30');
    expect(sipBarEnd('2026-10-15T15:16:00+00:00')).toBe('10:30');
    expect(sipBarEnd('2026-10-15T15:31:00+00:00')).toBe('11:30');
  });
});

describe('earningsChipLabel', () => {
  it('formats the date, missing dates and unavailable sources', () => {
    expect(
      earningsChipLabel({ date: '2026-10-29', sessions: 9, unavailable: false }),
    ).toBe('earnings 29 Oct');
    expect(
      earningsChipLabel({ date: null, sessions: null, unavailable: false }),
    ).toBe('no earnings date');
    expect(
      earningsChipLabel({ date: null, sessions: null, unavailable: true }),
    ).toBe('earnings unavailable');
    expect(earningsChipLabel(null)).toBe('earnings unavailable');
  });
});

describe('firstTouchLabel', () => {
  it('maps stored values to the drawer wording', () => {
    expect(firstTouchLabel('stop')).toBe('stop');
    expect(firstTouchLabel('target')).toBe('target');
    expect(firstTouchLabel('neither')).toBe('neither yet');
    expect(firstTouchLabel(null)).toBeNull();
    expect(firstTouchLabel(undefined)).toBeNull();
  });
});

describe('orderStatusText', () => {
  it('describes a filled bracket', () => {
    const text = orderStatusText(
      detail({
        execution_mode: 'automatic',
        orders: [
          {
            id: 1,
            alpaca_order_id: 'x',
            client_order_id: null,
            parent_alpaca_order_id: null,
            symbol: 'MSFT',
            leg: 'entry',
            side: 'buy',
            type: 'limit',
            qty: 4,
            limit_price: '418.40',
            stop_price: null,
            status: 'filled',
            created_at: '2026-10-15T14:46:52+00:00',
            filled_at: '2026-10-15T14:48:10+00:00',
            filled_avg_price: '418.20',
            fills: [
              {
                qty: 4,
                price: '418.20',
                slippage: '-0.20',
                filled_at: '2026-10-15T14:48:10+00:00',
              },
            ],
          },
          {
            id: 2,
            alpaca_order_id: 'y',
            client_order_id: null,
            parent_alpaca_order_id: 'x',
            symbol: 'MSFT',
            leg: 'stop',
            side: 'sell',
            type: 'stop',
            qty: 4,
            limit_price: null,
            stop_price: '409.60',
            status: 'new',
            created_at: '2026-10-15T14:48:10+00:00',
            filled_at: null,
            filled_avg_price: null,
            fills: [],
          },
          {
            id: 3,
            alpaca_order_id: 'z',
            client_order_id: null,
            parent_alpaca_order_id: 'x',
            symbol: 'MSFT',
            leg: 'target',
            side: 'sell',
            type: 'limit',
            qty: 4,
            limit_price: '435.40',
            stop_price: null,
            status: 'new',
            created_at: '2026-10-15T14:48:10+00:00',
            filled_at: null,
            filled_avg_price: null,
            fills: [],
          },
        ],
      }),
    );
    expect(text).toContain('Automatic · bracket sent');
    expect(text).toContain('filled 4 @ 418.20');
    expect(text).toContain('stop + target open at Alpaca');
  });

  it('aggregates split fills and reports exits rather than claiming no order', () => {
    const order = {
      id: 1, leg: 'entry', qty: 4, status: 'filled', created_at: '2026-10-15T14:46:00Z',
      filled_at: null, filled_avg_price: null,
      fills: [
        { qty: 1, price: '100', filled_at: '2026-10-15T14:47:00Z' },
        { qty: 3, price: '102', filled_at: '2026-10-15T14:48:00Z' },
      ],
    } as NonNullable<DecisionDetail['orders']>[number];
    expect(orderStatusText(detail({ orders: [order] }))).toContain('filled 4 @ 101.50');
    const exit = { ...order, leg: 'exit' };
    const text = orderStatusText(detail({ final_action: 'exit', orders: [exit] }));
    expect(text).toContain('exit filled at Alpaca');
    expect(text).not.toContain('No order');
    expect(orderStatusText(detail({ orders: [exit] }), true)).toContain('exit filled in backtest');
    expect(orderStatusText(detail({ orders: [exit] }), true)).not.toContain('Alpaca');
    expect(orderStatusText(detail({ orders: [{ ...order, qty: 10, status: 'partially_filled' }] })))
      .toContain('filled 4 of 10 @ 101.50');
  });

  it('describes a replacement stop leg instead of claiming no order', () => {
    const stop = {
      id: 2, alpaca_order_id: 's1', client_order_id: null, parent_alpaca_order_id: null,
      symbol: 'MSFT', leg: 'stop' as const, side: 'sell' as const, type: 'stop' as const,
      qty: 10, limit_price: null, stop_price: '102.40', status: 'new',
      created_at: '2026-10-15T15:46:00Z', filled_at: null, filled_avg_price: null, fills: [],
    };
    const text = orderStatusText(detail({ final_action: 'tighten_stop', orders: [stop] }));
    expect(text).toContain('stop new at Alpaca');
    expect(text).toContain('10 sh');
    expect(text).not.toContain('No order recorded');
  });

  it('describes a pending approval', () => {
    const text = orderStatusText(
      detail({
        execution_mode: 'supervised',
        approval: {
          id: 1,
          status: 'pending',
          reason: null,
          qty: 4,
          limit_price: '418.40',
          created_at: '2026-10-15T14:46:52+00:00',
          resolved_at: null,
        },
      }),
    );
    expect(text).toBe('Supervised · approval pending');
  });

  it('describes a no-call decision', () => {
    expect(
      orderStatusText(detail({ final_action: 'no_call' })),
    ).toBe('No order — no model call');
    expect(orderStatusText(detail({ final_action: 'skip' }))).toBe(
      'No order — skip',
    );
  });
});
