import { describe, expect, it } from 'vitest';

import type { HeaderState } from '../types/api';
import { documentTitle } from './documentTitle';

function header(over: Partial<HeaderState> & { sessionState?: string } = {}): HeaderState {
  const { sessionState = 'closed', ...rest } = over;
  return {
    paper: true,
    mode: 'live',
    session: {
      state: sessionState,
      display_time: '',
      next_cycle: null,
    },
    equity_usd: null,
    equity_eur: null,
    today_usd: null,
    today_eur: null,
    regime: 'neutral',
    kill_switch: false,
    paused: false,
    paused_since: null,
    ...rest,
  };
}

describe('documentTitle', () => {
  it('is "Stock Agent — <page>" per design for each route', () => {
    expect(documentTitle('/settings', null)).toBe('Stock Agent — Settings');
    expect(documentTitle('/universe', null)).toBe('Stock Agent — Universe');
    expect(documentTitle('/positions', null)).toBe(
      'Stock Agent — Positions & orders',
    );
    expect(documentTitle('/', null)).toBe('Stock Agent — Dashboard');
  });

  it('shows "Open" while the session is open', () => {
    expect(documentTitle('/', header({ sessionState: 'open' }))).toBe(
      'Stock Agent — Dashboard · Open',
    );
  });

  it('shows "Paused" when the agent is paused', () => {
    expect(documentTitle('/reviews', header({ paused: true }))).toBe(
      'Stock Agent — Reviews · Paused',
    );
  });

  it('shows "Kill switch" — it beats paused and open', () => {
    expect(
      documentTitle(
        '/',
        header({ kill_switch: true, paused: true, sessionState: 'open' }),
      ),
    ).toBe('Stock Agent — Dashboard · Kill switch');
  });

  it('shows no state suffix for a normal closed session', () => {
    expect(documentTitle('/', header())).toBe('Stock Agent — Dashboard');
  });
});
