/** LLM inspector filter model (docs/SPEC.md, LLM call history — Filters).
 *
 * The URL is the state: `?from=&to=&outcome=&source=&symbol=&action=&
 * reason=&model=&prompt=&strategy=&cycle=&call=` plus `period` and `order`
 * (UI conveniences that resolve to the API's from/to and ordering).
 * Everything here is pure so the page stays thin and the logic testable.
 */

import type { LlmCallRow, LlmCallSource } from '../types/api';

export type Period = 'last' | '5' | '20' | 'all' | 'custom';
export const PERIODS: { value: Period; label: string }[] = [
  { value: 'last', label: 'Last session' },
  { value: '5', label: '5 sessions' },
  { value: '20', label: '20 sessions' },
  { value: 'all', label: 'All' },
  { value: 'custom', label: 'Custom' },
];

export const SOURCES: { value: LlmCallSource; label: string }[] = [
  { value: 'live', label: 'Live decisions' },
  { value: 'shadow', label: 'Shadows' },
  { value: 'review', label: 'Reviews' },
];

/** spec default: shadows stay off (they triple the volume). */
export const DEFAULT_SOURCES: LlmCallSource[] = ['live', 'review'];

export const OUTCOMES = ['success', 'warning', 'error'] as const;

export const REASON_LABELS: Record<string, string> = {
  changed_by_code: 'changed by code',
  input_incomplete: 'input incomplete',
  number_check_failed: 'number check failed',
  unsupported_claim: 'unsupported claim',
  confidence_outlier: 'confidence outlier',
  unresolved_conflict: 'unresolved conflict',
  best_option_mismatch: 'best option mismatch',
  best_option_missing: 'best option missing',
  slow: 'slow',
  timeout: 'timeout',
  api_error: 'API error',
  invalid_output: 'invalid output',
  refusal: 'refusal',
};

export const REASON_OPTIONS = [
  'changed_by_code',
  'input_incomplete',
  'number_check_failed',
  'unsupported_claim',
  'confidence_outlier',
  'unresolved_conflict',
  'best_option_mismatch',
  'best_option_missing',
  'slow',
  'timeout',
  'api_error',
  'invalid_output',
  'refusal',
];

export const ACTION_OPTIONS = [
  'enter',
  'skip',
  'hold',
  'tighten_stop',
  'exit',
  'not_selected',
  'no_call',
];

export interface InspectorFilters {
  period: Period;
  /** Custom range (session dates, YYYY-MM-DD) — only when period=custom. */
  customFrom?: string;
  customTo?: string;
  outcome?: string;
  sources: LlmCallSource[];
  symbol?: string;
  action?: string;
  reason?: string;
  model?: string;
  prompt?: string;
  strategy?: string;
  cycle?: string;
  order: 'desc' | 'asc';
  call?: number;
}

export function parseFilters(params: URLSearchParams): InspectorFilters {
  const from = params.get('from') ?? undefined;
  const to = params.get('to') ?? undefined;
  const rawPeriod = params.get('period');
  const period: Period =
    rawPeriod === '5' ||
    rawPeriod === '20' ||
    rawPeriod === 'all' ||
    rawPeriod === 'custom'
      ? rawPeriod
      : rawPeriod === 'last'
        ? 'last'
        : from || to
          ? 'custom'
          : 'last';
  const rawSources = params.get('source');
  const call = params.get('call');
  return {
    period,
    customFrom: from,
    customTo: to,
    outcome: params.get('outcome') ?? undefined,
    sources:
      rawSources === null
        ? [...DEFAULT_SOURCES]
        : (rawSources.split(',').filter(Boolean) as LlmCallSource[]),
    symbol: params.get('symbol') ?? undefined,
    action: params.get('action') ?? undefined,
    reason: params.get('reason') ?? undefined,
    model: params.get('model') ?? undefined,
    prompt: params.get('prompt') ?? undefined,
    strategy: params.get('strategy') ?? undefined,
    cycle: params.get('cycle') ?? undefined,
    order: params.get('order') === 'asc' ? 'asc' : 'desc',
    call: call ? Number(call) : undefined,
  };
}

/** The effective session-date range for the chosen period. `dates` is the
 * summary's session calendar, newest first. */
export function resolveRange(
  filters: InspectorFilters,
  dates: string[],
): { from?: string; to?: string } {
  const last = dates[0];
  switch (filters.period) {
    case 'last':
      return last ? { from: last, to: last } : {};
    case '5':
      return last ? { from: dates[Math.min(4, dates.length - 1)], to: last } : {};
    case '20':
      return last ? { from: dates[Math.min(19, dates.length - 1)], to: last } : {};
    case 'all':
      return {};
    case 'custom':
      return { from: filters.customFrom, to: filters.customTo };
  }
}

/** Filters as the API takes them: the resolved date range plus the dims. */
export function apiFilters(
  filters: InspectorFilters,
  dates: string[],
): Record<string, string | undefined> {
  const { from, to } = resolveRange(filters, dates);
  return {
    from,
    to,
    outcome: filters.outcome,
    source:
      filters.sources.length === 0 ? 'none' : filters.sources.join(','),
    symbol: filters.symbol,
    action: filters.action,
    reason: filters.reason,
    model: filters.model,
    prompt: filters.prompt,
    strategy: filters.strategy,
    cycle: filters.cycle,
  };
}

/** The same filters as URL params (keeps `call` and `order` too). */
export function toSearchParams(filters: InspectorFilters): URLSearchParams {
  const params = new URLSearchParams();
  params.set('period', filters.period);
  if (filters.period === 'custom') {
    if (filters.customFrom) params.set('from', filters.customFrom);
    if (filters.customTo) params.set('to', filters.customTo);
  }
  if (filters.outcome) params.set('outcome', filters.outcome);
  // Defaults stay out of the URL so links stay short.
  const nonDefault =
    filters.sources.length !== DEFAULT_SOURCES.length ||
    DEFAULT_SOURCES.some((s) => !filters.sources.includes(s));
  if (nonDefault) {
    params.set('source', filters.sources.join(',') || 'none');
  }
  for (const key of ['symbol', 'action', 'reason', 'model', 'prompt', 'strategy', 'cycle'] as const) {
    if (filters[key]) params.set(key, filters[key]!);
  }
  if (filters.order !== 'desc') params.set('order', filters.order);
  if (filters.call !== undefined) params.set('call', String(filters.call));
  return params;
}

/** Number of "More" filters currently set (the phone button's count). */
export function moreFilterCount(filters: InspectorFilters): number {
  return ['symbol', 'action', 'reason', 'model', 'prompt', 'strategy', 'cycle']
    .filter((k) => Boolean(filters[k as keyof InspectorFilters]))
    .length;
}

/** Group list rows under their session dates, keeping the page's order. */
export function groupBySession(
  calls: LlmCallRow[],
): { date: string; calls: LlmCallRow[] }[] {
  const groups: { date: string; calls: LlmCallRow[] }[] = [];
  for (const call of calls) {
    const last = groups[groups.length - 1];
    if (last && last.date === call.session_date) {
      last.calls.push(call);
    } else {
      groups.push({ date: call.session_date, calls: [call] });
    }
  }
  return groups;
}

/** The row's left column: symbol for decisions, the kind for reviews
 * ("Briefing", "Weekly" or "Verdict" — design/screens/Inspector.dc.html). */
export function rowTitle(call: LlmCallRow): string {
  if (call.source === 'review') {
    return { briefing: 'Briefing', weekly: 'Weekly', verdict: 'Verdict' }[
      call.kind
    ] ?? 'Review';
  }
  return call.symbol ?? '—';
}

/** The details title's review name ("Daily briefing", "Weekly review" or
 * "Verdict draft"). */
export function reviewTitle(kind: string): string {
  return { briefing: 'Daily briefing', weekly: 'Weekly review', verdict: 'Verdict draft' }[
    kind
  ] ?? 'Review call';
}

/** The row's context part under the symbol: context or review prompt. */
export function rowContext(call: LlmCallRow): string {
  if (call.source === 'review') return `review · ${call.prompt_version ?? '—'}`;
  if (call.source === 'shadow') {
    return `shadow · ${call.strategy ?? '—'} · ${call.context ?? 'candidate'}`;
  }
  return call.context ?? 'candidate';
}

/** The outcome chip's text: "success", or the reason labels. */
export function chipText(call: LlmCallRow): string {
  if (call.outcome === 'success') return 'success';
  return call.reasons.map((r) => REASON_LABELS[r] ?? r).join(', ');
}
