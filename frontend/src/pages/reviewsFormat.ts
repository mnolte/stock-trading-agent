import type {
  ReviewDetail,
  ReviewListItem,
  ThesisCheck,
  VerdictValue,
  WeeklyFacts,
} from '../types/api';
import type { ChipVariant } from '../components/Chip';

const DISPLAY_TZ = 'Europe/Amsterdam';

/** "+1.49%" / "−0.49%" — signed percent, null → "—". */
export function signedPct(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toFixed(digits)}%`;
}

/** "+0.76 pts" — signed percentage-point figure, null → "—". */
export function signedPts(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toFixed(2)} pts`;
}

/** "+1.07R" — signed R figure, null → "—". */
export function signedR(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toFixed(2)}R`;
}

/** "$10,148.80" — USD with thousands separators. */
export function usd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—';
  return `$${value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

/** "+0.73%" with colour hint — positive/negative/muted variant for numbers. */
export function numVariant(
  value: number | null | undefined,
): 'positive' | 'negative' | 'neutral' {
  if (value === null || value === undefined) return 'neutral';
  return value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral';
}

/** "Tue 6" — weekday + day for a date-only ("2025-10-06") or ISO datetime. */
export function dayLabel(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = iso.length === 10 ? new Date(`${iso}T12:00:00`) : new Date(iso);
  return d.toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    timeZone: DISPLAY_TZ,
  });
}

/** "Wed 7 Oct" — weekday + day + month in Amsterdam time. */
export function decidedLabel(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: DISPLAY_TZ,
  });
}

/** "22 Oct" — day + month in Amsterdam time (verdict due date). */
export function dueDayLabel(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    timeZone: DISPLAY_TZ,
  });
}

/** "Sat 10 Oct 10:00" — weekday + date + time in Amsterdam time. */
export function writtenLabel(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const date = d.toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: DISPLAY_TZ,
  });
  const time = d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: DISPLAY_TZ,
  });
  return `${date} ${time}`;
}

/** "Week 41 · 5 – 9 Oct" from weekly facts. */
export function weekLabel(facts: WeeklyFacts): string {
  const end = new Date(`${facts.period.end}T12:00:00`);
  const startDay = new Date(`${facts.period.start}T12:00:00`);
  const endStr = end.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
  });
  return `Week ${facts.period.week} · ${startDay.getDate()} – ${endStr}`;
}

/** "12 – 16 Oct" from a YYYY-MM-DD range. */
export function rangeLabel(startIso: string, endIso: string): string {
  const start = new Date(`${startIso}T12:00:00`);
  const end = new Date(`${endIso}T12:00:00`);
  const endStr = end.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
  });
  return `${start.getDate()} – ${endStr}`;
}

/**
 * Meta line under a review title:
 * "Written Sat 10 Oct 10:00 · gpt-4o-mini · weekly.v1 · numbers checked ✓"
 * or "Written Sat 10 Oct 10:00 · Plain review: <reason>".
 */
export function metaLine(review: ReviewListItem | ReviewDetail): string {
  const parts = [`Written ${writtenLabel(review.due_at)}`];
  if (review.model) parts.push(review.model);
  if (review.prompt_version) parts.push(review.prompt_version);
  if (review.source === 'model') parts.push('numbers checked ✓');
  else parts.push(`Plain review: ${review.reason}`);
  return parts.join(' · ');
}

export function verdictLabel(v: VerdictValue): string {
  switch (v) {
    case 'good_call':
      return 'Good call';
    case 'bad_call':
      return 'Bad call';
    default:
      return 'Unclear';
  }
}

export function verdictVariant(v: VerdictValue): ChipVariant {
  switch (v) {
    case 'good_call':
      return 'positive';
    case 'bad_call':
      return 'negative';
    default:
      return 'neutral';
  }
}

/** Right-hand context in a verdict row ("target +1.6R"), from the API. */
export function verdictOutcomeText(outcome: string | null): string {
  return outcome ?? '—';
}

/** "ENTER B" / "SKIP" — the model's call next to a verdict row. */
export function modelCallText(
  modelAction: string | null | undefined,
  optionId: string | null | undefined,
): string {
  const action = (modelAction ?? '').toUpperCase() || '—';
  return optionId ? `${action} ${optionId}` : action;
}

/** The thesis-check chip text and variant (design: verdict rows). */
export function thesisCheckChip(check: ThesisCheck | null | undefined): {
  text: string;
  variant: ChipVariant;
} | null {
  if (check === 'contradicts') {
    return { text: 'contradicts the facts', variant: 'warning' };
  }
  if (check === 'consistent') {
    return { text: 'consistent with the facts', variant: 'neutral' };
  }
  if (check === 'unclear') {
    return { text: 'unclear', variant: 'neutral' };
  }
  return null;
}

/** A weekly suggestion body: v2 {text, evidence} or a v1 string. */
export function suggestionParts(
  suggestion:
    | { text: string; evidence: string }
    | string
    | null
    | undefined,
): { text: string; evidence: string | null } | null {
  if (suggestion == null) return null;
  if (typeof suggestion === 'string') {
    return suggestion ? { text: suggestion, evidence: null } : null;
  }
  return { text: suggestion.text, evidence: suggestion.evidence };
}

/**
 * The "No suggestion yet" line (design: "No suggestion yet: 3 closed
 * trades in this run, 10 needed.") — null when a suggestion exists.
 */
export function noSuggestionText(facts: WeeklyFacts): string | null {
  const have = facts.sample?.closed_trades.run ?? 0;
  const need =
    (facts.limits?.min_closed_trades_for_suggestion as number | undefined) ??
    10;
  return `No suggestion yet: ${have} closed trade${
    have === 1 ? '' : 's'
  } in this run, ${need} needed.`;
}

/** ISO week number for a YYYY-MM-DD date (used for the upcoming due row). */
export function isoWeek(isoDate: string): number {
  const d = new Date(`${isoDate}T12:00:00`);
  const target = new Date(d.valueOf());
  const dayNr = (d.getDay() + 6) % 7;
  target.setDate(target.getDate() - dayNr + 3);
  const firstThursday = new Date(target.getFullYear(), 0, 4);
  const firstDayNr = (firstThursday.getDay() + 6) % 7;
  firstThursday.setDate(firstThursday.getDate() - firstDayNr + 3);
  return 1 + Math.round((target.getTime() - firstThursday.getTime()) / 604800000);
}

export function addDays(isoDate: string, days: number): string {
  const d = new Date(`${isoDate}T12:00:00`);
  d.setDate(d.getDate() + days);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/**
 * The upcoming weekly review row: the Saturday it is due covers the
 * Monday–Friday immediately before it. ``period`` matches the review
 * row's period id so a week already written can be skipped.
 */
export function upcomingWeek(dueIso: string): {
  label: string;
  sub: string;
  due: string;
  period: string;
} {
  const dueDate = dueIso.slice(0, 10);
  const friday = addDays(dueDate, -1);
  const monday = addDays(dueDate, -5);
  // ISO year = the year of this week's Thursday (differs at year edges).
  const thursday = new Date(`${monday}T12:00:00`);
  thursday.setDate(thursday.getDate() + 3);
  return {
    label: `Week ${isoWeek(monday)}`,
    sub: rangeLabel(monday, friday),
    due: `due ${decidedLabel(dueIso)}`,
    period: `${thursday.getFullYear()}-W${String(isoWeek(monday)).padStart(2, '0')}`,
  };
}
