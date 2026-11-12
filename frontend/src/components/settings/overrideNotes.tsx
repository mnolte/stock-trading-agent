import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

// Shadow override notes (item 069): when an active shadow runs a
// strategy-overridable setting differently from live, the row's
// explanation ends with "Shadow A uses 45–65", linking to that
// strategy on the Strategies page.

export interface OverrideEntry {
  id: number;
  name: string;
  value: unknown;
}

function fmtOverrideValue(value: unknown): string {
  if (value === null || value === undefined) return 'Off';
  if (typeof value === 'boolean') return value ? 'On' : 'Off';
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'number' && Number.isInteger(value)) return String(value);
  return String(value);
}

/** Notes for one row; ``paths`` are the settings paths the row edits
 * (a pair like rsi_min + rsi_max renders as "45–65"). */
export function OverrideNotes({
  overrides,
  paths,
}: {
  overrides: Record<string, OverrideEntry[]> | undefined;
  paths: string[];
}): ReactNode {
  if (!overrides) return null;
  // Group the row's paths per shadow, keeping shadow order.
  const byShadow = new Map<number, { name: string; values: unknown[] }>();
  for (const path of paths) {
    for (const entry of overrides[path] ?? []) {
      const slot = byShadow.get(entry.id) ?? { name: entry.name, values: [] };
      slot.values.push(entry.value);
      byShadow.set(entry.id, slot);
    }
  }
  if (byShadow.size === 0) return null;
  return (
    <>
      {[...byShadow.entries()].map(([id, shadow]) => {
        const allNumeric = shadow.values.every((v) => typeof v === 'number');
        const shown =
          shadow.values.length > 1 && allNumeric
            ? `${fmtOverrideValue(shadow.values[0])}–${fmtOverrideValue(shadow.values[shadow.values.length - 1])}`
            : shadow.values.map(fmtOverrideValue).join(', ');
        return (
          <span key={id}>
            {' · '}
            <Link
              to={`/strategies?strategy=${id}`}
              style={{ color: 'var(--sa-accent)' }}
            >
              {shadow.name} uses {shown}
            </Link>
          </span>
        );
      })}
    </>
  );
}
