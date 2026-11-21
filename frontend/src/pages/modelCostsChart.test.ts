import { describe, expect, it } from 'vitest';

import {
  LABEL_MIN_GAP,
  labelPitch,
  sessionSentence,
  thinnedLabels,
  type SessionBucket,
  type SessionSummary,
} from './modelCostsFormat';

function bucket(partial: Partial<SessionBucket>): SessionBucket {
  return {
    key: '2026-12-14',
    label: '14',
    from: '2026-12-14',
    to: '2026-12-14',
    sessions: 1,
    live: '0',
    shadows: '0',
    reviews: '0',
    ask: '0',
    ...partial,
  };
}

function summary(
  average: string | null = '0.5100',
  highest: {
    key?: string;
    total?: string;
    largest_part?: string | null;
  } | null = {},
): SessionSummary {
  return {
    average_per_session: average,
    highest:
      highest == null
        ? null
        : {
            key: highest.key ?? '2026-12-14',
            total: highest.total ?? '0.75',
            largest_part:
              highest.largest_part === undefined ? 'ask' : highest.largest_part,
          },
  };
}

/** Every shown label sits at least LABEL_MIN_GAP px from the previous
 * shown one — except the last, which is always shown (spec → Fit). */
function expectThinning(show: boolean[], pitch: number) {
  expect(show[0]).toBe(true);
  expect(show[show.length - 1]).toBe(true);
  let last = 0;
  for (let i = 1; i < show.length - 1; i++) {
    if (!show[i]) continue;
    expect((i - last) * pitch).toBeGreaterThanOrEqual(LABEL_MIN_GAP - 1e-9);
    last = i;
  }
}

describe('thinnedLabels', () => {
  // Plot widths roughly inside the card at each viewport (axis and
  // padding aside), 31 bars — the 31-bar cap.
  it.each([
    ['390 px', 340, 2],
    ['1024 px', 940, 6],
    ['1440 px', 1340, 6],
  ])('keeps labels %s apart at %i bars', (_name, width, gap) => {
    const n = 31;
    const pitch = labelPitch(width, n, gap);
    const show = thinnedLabels(n, pitch);
    expectThinning(show, pitch);
    if (width === 1340) {
      // Roomy enough that every slot clears 36 px.
      expect(show.every(Boolean)).toBe(true);
    } else {
      expect(show.some((s) => !s)).toBe(true);
    }
  });

  it('always shows the first and the last', () => {
    for (const pitch of [5, 12, 20, 40]) {
      const show = thinnedLabels(9, pitch);
      expect(show[0]).toBe(true);
      expect(show[8]).toBe(true);
    }
  });

  it('shows everything when slots are wide', () => {
    expect(thinnedLabels(5, 40)).toEqual([true, true, true, true, true]);
  });

  it('handles one and two bars', () => {
    expect(thinnedLabels(1, 10)).toEqual([true]);
    expect(thinnedLabels(2, 10)).toEqual([true, true]);
    expect(thinnedLabels(0, 10)).toEqual([]);
  });
});

describe('sessionSentence', () => {
  it('session unit', () => {
    const rows = [bucket({})];
    expect(sessionSentence('session', rows, summary())).toBe(
      'Average $0.51 a session; the highest was $0.75 on Mon 14 Dec, with Ask the largest part.',
    );
  });

  it('week unit', () => {
    const rows = [
      bucket({
        key: '2026-W43',
        label: 'W43',
        from: '2026-10-19',
        to: '2026-10-23',
      }),
    ];
    expect(
      sessionSentence(
        'week',
        rows,
        summary('0.5100', { key: '2026-W43', total: '0.75', largest_part: 'shadows' }),
      ),
    ).toBe(
      'Average $0.51 a session; the highest week was W43 (19–23 Oct), with Shadows the largest part.',
    );
  });

  it('month unit', () => {
    const rows = [
      bucket({
        key: '2026-10',
        label: 'Oct',
        from: '2026-10-01',
        to: '2026-10-30',
      }),
    ];
    expect(sessionSentence('month', rows, summary('0.5100', { key: '2026-10' }))).toBe(
      'Average $0.51 a session; the highest month was October 2026, with Ask the largest part.',
    );
  });

  it('quarter unit', () => {
    const rows = [
      bucket({
        key: '2026-Q4',
        label: 'Q4',
        from: '2026-10-01',
        to: '2026-12-31',
      }),
    ];
    expect(sessionSentence('quarter', rows, summary('0.5100', { key: '2026-Q4' }))).toBe(
      'Average $0.51 a session; the highest quarter was Q4 2026, with Ask the largest part.',
    );
  });

  it('degrades without a summary or a highest bucket', () => {
    expect(sessionSentence('session', [bucket({})], null)).toBe('');
    expect(sessionSentence('session', [bucket({})], summary('0.5100', null))).toBe(
      'Average $0.51 a session.',
    );
  });
});
