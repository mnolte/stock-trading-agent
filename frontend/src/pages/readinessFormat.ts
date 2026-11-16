import type { ChipVariant } from '../components/Chip';
import type { ReadinessCriterion } from '../types/api';

/** Item 100 wording map: the stored statuses stay as they are; the
 * screen words them by what is needed (docs/SPEC.md → Readiness).
 * Counts that grow by themselves read "building up" (blue). */
export const BUILDING_UP = new Set(['closed_trades', 'sessions']);

export function statusVariant(c: ReadinessCriterion): ChipVariant {
  if (c.status === 'pass') return 'positive';
  if (c.status === 'waiting') return 'neutral';
  return BUILDING_UP.has(c.key) ? 'info' : 'negative';
}

export function statusText(c: ReadinessCriterion): string {
  if (c.status === 'pass') return 'pass';
  if (c.status === 'waiting') return 'needs data';
  return BUILDING_UP.has(c.key) ? 'building up' : 'below target';
}

/** The sub line for a needs-data row: "needs 30 closed trades · 3 so
 * far" from the criterion's minimum sample and current count. */
export function criterionSub(c: ReadinessCriterion): string | null {
  if (c.status === 'waiting' && c.needs) {
    return `needs ${c.needs} · ${c.sample ?? 0} so far`;
  }
  return c.sub;
}
