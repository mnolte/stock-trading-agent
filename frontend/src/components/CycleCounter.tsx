import { useEffect, useRef, useState } from 'react';

import type { CycleCounter as CycleCounterData, CycleCounterSegment } from '../types/api';
import { joinParts } from '../format';
import {
  checkLabel,
  counterCountdown,
  cycleProgressLabel,
  nextCycleParts,
  releaseLine,
  runLabel,
  segmentTitle,
} from './cycleCounterFormat';

function Segment({ segment }: { segment: CycleCounterSegment }) {
  return (
    <span
      title={segmentTitle(segment)}
      style={{
        flex: '1 1 0',
        height: '8px',
        borderRadius: '4px',
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

/** The cycle strip's right column: the next cycle with a live countdown,
 * one segment per cycle of the shown session, "Cycle n of 6 today",
 * the cycle's number in this run and the next position check. The
 * schedule comes from the backend; only the countdown ticks client-side
 * (it resyncs when the header topic refetches the dashboard). */
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

  const gapMs = counter.next_cycle
    ? new Date(counter.next_cycle).getTime() - nowMs
    : Infinity;
  const countdown = counter.running
    ? 'running'
    : counter.next_cycle && gapMs <= 24 * 3_600_000
      ? counterCountdown(counter.next_cycle, nowMs)
      : null;
  const next = nextCycleParts(counter);
  const progress = cycleProgressLabel(counter);
  const run = runLabel(counter);
  const check = checkLabel(counter);
  const release = counter.next_release
    ? releaseLine(counter.next_release)
    : null;

  return (
    <div
      role="timer"
      aria-label="Cycle counter"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        color: 'var(--sa-text-2)',
        fontSize: 'var(--sa-text-md)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: '10px',
          flexWrap: 'wrap',
        }}
      >
        <span>
          {next ? (
            <>
              Next cycle{' '}
              <b style={{ fontWeight: 600, color: 'var(--sa-text)' }}>
                {next.when}
              </b>{' '}
              <span style={{ color: 'var(--sa-text-muted)' }}>· {next.ny}</span>
            </>
          ) : (
            'no cycle scheduled'
          )}
        </span>
        {countdown && (
          <span
            style={{
              fontFamily: 'var(--sa-font-mono)',
              fontSize: 'var(--sa-text-base)',
              color: 'var(--sa-text)',
            }}
          >
            {countdown}
          </span>
        )}
      </div>
      {counter.segments.length > 0 && (
        <div aria-hidden="true" style={{ display: 'flex', gap: '4px' }}>
          {counter.segments.map((s) => (
            <Segment key={s.time} segment={s} />
          ))}
        </div>
      )}
      {(progress || run || check) && (
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: 'space-between',
            gap: '4px 10px',
            fontSize: 'var(--sa-text-sm)',
            color: 'var(--sa-text-muted)',
          }}
        >
          <span>{joinParts(progress, run)}</span>
          <span>{check}</span>
        </div>
      )}
      {release && (
        <div
          title="Economic release within 2 sessions (item 067). No rule for 08:30 releases: entries and positions held into it are tagged release_next_morning."
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: '8px',
            paddingTop: '8px',
            borderTop: '1px solid var(--sa-border)',
            fontSize: 'var(--sa-text-sm)',
            color: 'var(--sa-text-2)',
          }}
        >
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              height: '24px',
              padding: '0 8px',
              borderRadius: 'var(--sa-radius-sm)',
              background: 'var(--sa-warning-bg)',
              border: '1px solid var(--sa-warning-border)',
              color: 'var(--sa-warning-text)',
              fontSize: 'var(--sa-text-sm)',
              whiteSpace: 'nowrap',
            }}
          >
            Release
          </span>
          <span>
            {release.lead}{' '}
            {release.ams && (
              <span style={{ fontFamily: 'var(--sa-font-mono)' }}>{release.ams}</span>
            )}{' '}
            <span style={{ color: 'var(--sa-text-muted)' }}>· {release.tail}</span>
          </span>
        </div>
      )}
    </div>
  );
}
