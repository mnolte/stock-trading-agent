import { describe, expect, it } from 'vitest';

import {
  dayParts,
  fmtDay,
  fmtDayMonthIn,
  fmtDayYear,
  fmtMonthYear,
  fmtRange,
  fmtWeekdayDay,
  parseDayInput,
} from './format';

// 056 — dates read the same everywhere: fixed English three-letter month
// names, never the browser locale ("Sept", "sep."), and the same output no
// matter which locale the browser runs in. September is the probe month:
// en-GB renders it "Sept" and nl-NL "sep" — the table must always say "Sep".

describe('fmtDay / fmtDayYear', () => {
  it('drops the year inside the current year, keeps it otherwise', () => {
    const now = new Date();
    const thisYear = `${now.getUTCFullYear()}-03-14`;
    const otherYear = now.getUTCFullYear() === 2025 ? '2026-03-14' : '2025-03-14';
    expect(fmtDay(thisYear)).toBe('14 Mar');
    expect(fmtDay(otherYear)).toBe(`14 Mar ${otherYear.slice(0, 4)}`);
    expect(fmtDayYear(thisYear)).toBe(`14 Mar ${thisYear.slice(0, 4)}`);
  });

  it('uses fixed English months, never "Sept"', () => {
    expect(fmtDayYear('2025-09-14')).toBe('14 Sep 2025');
    expect(fmtDayMonthIn('2025-09-14')).toBe('14 Sep');
    expect(fmtMonthYear('2025-09-01')).toBe('Sep 2025');
  });
});

describe('fmtRange', () => {
  it('same month: "14–25 Sep 2026"', () => {
    expect(fmtRange('2020-09-14', '2020-09-25')).toBe('14–25 Sep 2020');
  });

  it('same year, different months: "28 Sep – 2 Oct"', () => {
    expect(fmtRange('2020-09-28', '2020-10-02')).toBe('28 Sep – 2 Oct 2020');
  });

  it('crossing the year boundary spells both years', () => {
    expect(fmtRange('2025-12-28', '2026-01-02')).toBe(
      '28 Dec 2025 – 2 Jan 2026',
    );
  });

  it('inside the current year the year is dropped', () => {
    const y = new Date().getUTCFullYear();
    expect(fmtRange(`${y}-09-14`, `${y}-09-25`)).toBe('14–25 Sep');
  });
});

describe('fmtWeekdayDay', () => {
  it('spells the weekday and fixed month', () => {
    // 2025-09-14 was a Sunday.
    expect(fmtWeekdayDay('2025-09-14')).toBe('Sunday 14 Sep 2025');
  });
});

describe('parseDayInput', () => {
  it('reads the display format back to ISO', () => {
    expect(parseDayInput('14 Sep 2025')).toBe('2025-09-14');
    expect(parseDayInput('14 September 2025')).toBe('2025-09-14');
    expect(parseDayInput('2025-09-14')).toBe('2025-09-14');
    expect(parseDayInput('14/09/2025')).toBe('2025-09-14');
  });

  it('rejects text that is not a date', () => {
    expect(parseDayInput('')).toBeNull();
    expect(parseDayInput('next week')).toBeNull();
    expect(parseDayInput('32 Foo 2025')).toBeNull();
    expect(parseDayInput('14 Sept 2025')).toBe('2025-09-14');
  });
});

describe('dayParts', () => {
  it('reads date-only strings without a timezone shift', () => {
    expect(dayParts('2025-09-14')).toEqual({ y: 2025, m: 9, d: 14 });
  });
});
