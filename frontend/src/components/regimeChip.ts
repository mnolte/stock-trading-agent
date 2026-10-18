import type { ChipVariant } from './Chip';

/** Chip colour for the header regime label ("Up · normal vol" / "unavailable"). */
export function regimeVariant(label: string | null | undefined): ChipVariant {
  const trend = label?.split(' ')[0]?.toLowerCase();
  if (trend === 'up') return 'positive';
  if (trend === 'down') return 'negative';
  return 'neutral';
}
