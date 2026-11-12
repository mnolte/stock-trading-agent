import type { CSSProperties } from 'react';

import type { DashboardCycleLine } from '../types/api';
import { formatDuration } from '../format';
import { cycleCost } from './cycleLineFormat';

const VALUE_LABEL: CSSProperties = {
  fontSize: 'var(--sa-text-xs)',
  color: 'var(--sa-text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
};

const VALUE_UNDERLINE: CSSProperties = {
  textDecoration: 'underline dotted',
  textUnderlineOffset: '3px',
  cursor: 'help',
};

/** The strip's second part: Checked · Passed · Model calls · Cost ·
 * Took as labelled values (item 073). */
export function CycleValues({
  line,
  callsPerCycle,
}: {
  line: DashboardCycleLine;
  callsPerCycle: number | null;
}) {
  const s = line.summary;
  if (!s) return null;
  const items: {
    label: string;
    value: string;
    title?: string;
    warn?: boolean;
  }[] = [
    { label: 'Checked', value: String(s.checked) },
    {
      label: 'Passed',
      value: String(s.passed),
      title: 'Passed the rules pre-filter',
    },
    {
      label: 'Model calls',
      value:
        callsPerCycle !== null ? `${s.calls} of ${callsPerCycle}` : String(s.calls),
      title: `Held positions first, then candidates in rank order, at most ${callsPerCycle ?? 'the configured'} per cycle`,
    },
    { label: 'Cost', value: cycleCost(s.cost) },
    {
      label: 'Took',
      value: formatDuration(s.took_seconds),
      title: 'The whole cycle',
      warn: s.took_warn,
    },
  ];
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px 28px' }}>
      {items.map((it) => (
        <div
          key={it.label}
          style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}
        >
          <span style={it.title ? { ...VALUE_LABEL, ...VALUE_UNDERLINE } : VALUE_LABEL} title={it.title}>
            {it.label}
          </span>
          <span
            style={{
              fontFamily: 'var(--sa-font-mono)',
              fontSize: 'var(--sa-text-base)',
              color: it.warn ? 'var(--sa-warning-text)' : 'var(--sa-text)',
              whiteSpace: 'nowrap',
            }}
          >
            {it.value}
          </span>
        </div>
      ))}
    </div>
  );
}
