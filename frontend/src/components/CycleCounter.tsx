import { useEffect, useRef, useState } from 'react';

import type { CycleCounter as CycleCounterData, CycleCounterSegment } from '../types/api';
import {
  checkLabel,
  counterTail,
  nextCycleLabel,
  releaseLine,
  runLabel,
  segmentTitle,
  todayLabel,
} from './cycleCounterFormat';

function Segment({ segment }: { segment: CycleCounterSegment }) {
  return (
    <span
      title={segmentTitle(segment)}
      style={{
        width: '12px',
        height: '6px',
        borderRadius: '3px',
        boxSizing: 'border-box',
        background:
          segment.status === 'done'
            ? 'var(--sa-neutral-chip)'
            : segment.status === 'later'
              ? 'var(--sa-border)'
              : 'transparent',
        boxShadow:
          segment.status === 'next'
            ? 'inset 0 0 0 2px var(--sa-accent)'
            : 'none',
      }}
    />
  );
}

/** Right end of the this-cycle strip: next cycle with a live countdown,
 * one segment per cycle of today's session, "Cycle n of 6 today", the
 * cycle's number in this run and the next position check. The schedule
 * comes from the backend; only the countdown ticks client-side (it
 * resyncs when the header topic refetches the dashboard). */
export function CycleCounter({ counter }: { counter: CycleCounterData }) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  // Drift between the server clock and the client clock, measured on each
  // fresh payload, so the countdown tracks the server's idea of now.
  const skew = useRef(0);
  useEffect(() => {
    skew.current = counter.now ? Date.parse(counter.now) - Date.now() : 0;
  }, [counter.now]);
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now() + skew.current), 1_000);
    return () => clearInterval(timer);
  }, []);

  if (counter.kill_switch) {
    return (
      <span style={{ color: 'var(--sa-accent)' }}>Kill switch on · no cycles</span>
    );
  }

  const tail = counterTail(counter, nowMs);
  const check = checkLabel(counter);
  const run = runLabel(counter);
  const today = todayLabel(counter);
  const release = counter.next_release
    ? releaseLine(counter.next_release)
    : null;

  return (
    <span
      style={{
        display: 'inline-flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        gap: '2px',
        color: 'var(--sa-text-muted)',
        fontSize: 'var(--sa-text-md)',
      }}
    >
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 'var(--sa-space-2) var(--sa-space-3)',
        }}
      >
        <span>
          {nextCycleLabel(counter)}
          {tail && (
            <span style={{ fontFamily: 'var(--sa-font-mono)' }}> · {tail}</span>
          )}
        </span>
        {counter.segments.length > 0 && (
          <span
            aria-label="Today's cycles"
            style={{ display: 'inline-flex', gap: '3px', alignItems: 'center' }}
          >
            {counter.segments.map((s) => (
              <Segment key={s.time} segment={s} />
            ))}
          </span>
        )}
        {today && <span>{today}</span>}
        {run && (
          <span title="Cycles run since this run started (a new run restarts the count)">
            {run}
          </span>
        )}
        {check && <span>{check}</span>}
      </span>
      {release && (
        <span
          title="Economic release within 2 sessions (item 067). No rule for 08:30 releases: entries and positions held into it are tagged release_next_morning."
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: 'var(--sa-text-sm)',
            color: 'var(--sa-text-2)',
          }}
        >
          <span>
            {release.lead}{' '}
            {release.ams && (
              <span style={{ fontFamily: 'var(--sa-font-mono)' }}>{release.ams}</span>
            )}{' '}
            <span style={{ color: 'var(--sa-text-muted)' }}>· {release.tail}</span>
          </span>
        </span>
      )}
    </span>
  );
}
