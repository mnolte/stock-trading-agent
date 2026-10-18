import { describe, expect, it } from 'vitest';

import { regimeVariant } from './regimeChip';

describe('regimeVariant', () => {
  it('is positive for an up trend', () => {
    expect(regimeVariant('Up · normal vol')).toBe('positive');
  });
  it('is negative for a down trend, including the entry-blocking regime', () => {
    expect(regimeVariant('Down · high vol')).toBe('negative');
    expect(regimeVariant('Down · low vol')).toBe('negative');
  });
  it('is neutral for sideways, unavailable and missing regimes', () => {
    expect(regimeVariant('Sideways · normal vol')).toBe('neutral');
    expect(regimeVariant('Sideways · unknown vol')).toBe('neutral');
    expect(regimeVariant('unavailable')).toBe('neutral');
    expect(regimeVariant(null)).toBe('neutral');
    expect(regimeVariant(undefined)).toBe('neutral');
  });
});
