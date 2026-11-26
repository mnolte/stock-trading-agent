import { describe, expect, it } from 'vitest';

import { cycleCost, cycleSentence } from './cycleLineFormat';
import type { DashboardCycleLine } from '../types/api';

const line = (over: Partial<DashboardCycleLine>): DashboardCycleLine => ({
  label: '18:46 cycle',
  status: 'done',
  text: 'fallback text',
  cycle_index: 3,
  cycles_today: 6,
  time: '2026-10-15T16:46:00Z', // 18:46 Amsterdam · 12:46 New York
  cycle_id: 7,
  summary: {
    checked: 69,
    passed: 8,
    calls: 8,
    cost: '0.00312',
    took_seconds: 41,
    took_warn: false,
    changes: 'nothing changed',
    held: 2,
    skipped: 6,
    entered: 0,
    blocked: 0,
    approved: 0,
  },
  ...over,
});

describe('cycleSentence', () => {
  it('builds the design sentence from the summary numbers', () => {
    expect(cycleSentence(line({}))).toBe(
      '18:46 cycle · 12:46 NY · 69 symbols checked, 8 passed the pre-filter and 8 asked the model: both positions held, 6 skipped. Nothing changed.',
    );
  });
  it('says "every candidate skipped" when all calls skipped', () => {
    const l = line({});
    l.summary = { ...l.summary!, skipped: 8 };
    expect(cycleSentence(l)).toContain('every candidate skipped');
  });
  it('ends with the changes clause', () => {
    const l = line({});
    l.summary = { ...l.summary!, changes: '1 entry: MSFT', entered: 1 };
    expect(cycleSentence(l)).toContain('6 skipped. 1 entry: MSFT.');
  });
  it('falls back to the backend text without a summary', () => {
    expect(cycleSentence(line({ summary: null }))).toBe(
      '18:46 cycle · 12:46 NY · fallback text',
    );
    expect(cycleSentence(line({ summary: null, time: null }))).toBe(
      '18:46 cycle · fallback text',
    );
  });
});

describe('cycleCost', () => {
  it('formats the cost as a number', () => {
    expect(cycleCost('0.00312')).toBe('$0.003');
    expect(cycleCost('0.011')).toBe('$0.011');
    expect(cycleCost('0')).toBe('$0.000');
    expect(cycleCost('not-a-number')).toBe('—');
  });
});
