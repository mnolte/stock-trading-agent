/** Formatting helpers for the inspector and decision detail screens. */

import { ACTIONS, NO_CALL_REASONS, REASON_CODES, labelFor } from '../labels';

export function actionColor(action: string | null): string {
  switch (action) {
    case 'enter':
      return 'var(--sa-positive)';
    case 'skip':
      return 'var(--sa-accent)';
    case 'exit':
      return 'var(--sa-negative-text)';
    case 'tighten_stop':
      return 'var(--sa-info)';
    case 'hold':
      return 'var(--sa-text-2)';
    case 'briefing':
    case 'weekly':
    case 'verdict':
      return 'var(--sa-neutral-chip)';
    default:
      return 'var(--sa-text-disabled)';
  }
}

export function contextLabel(context: string): string {
  return context === 'held' ? 'Held position' : 'Entry candidate';
}

/** Big action badge colours (Latest decision card, decision drawer). */
export function actionBadgeStyle(action: string): { bg: string; color: string } {
  switch (action) {
    case 'enter':
      return { bg: 'var(--sa-positive-bg)', color: 'var(--sa-positive)' };
    case 'exit':
      return { bg: 'var(--sa-negative-bg)', color: 'var(--sa-negative-text)' };
    case 'skip':
      return { bg: 'var(--sa-warning-bg)', color: 'var(--sa-accent)' };
    case 'tighten_stop':
      return { bg: 'var(--sa-info-bg)', color: 'var(--sa-info)' };
    case 'blocked':
      return { bg: 'var(--sa-negative-bg)', color: 'var(--sa-negative-text)' };
    case 'hold':
      return { bg: 'var(--sa-subtle)', color: 'var(--sa-text-2)' };
    default:
      return { bg: 'var(--sa-subtle)', color: 'var(--sa-text-muted)' };
  }
}

export function actionLabel(action: string): string {
  switch (action) {
    case 'enter':
      return 'Enter long';
    case 'skip':
      return 'Skip';
    case 'hold':
      return 'Hold';
    case 'tighten_stop':
      return 'Tighten stop';
    case 'exit':
      return 'Exit';
    case 'no_call':
      return 'No call';
    default:
      return labelFor(ACTIONS, action);
  }
}

/** Why a decision recorded no call: rules, call cap, quote missing, model error. */
export function noCallReasonLabel(reason: string): string {
  return labelFor(REASON_CODES, reason);
}

/** "pre-filter: RSI 74 > 70" — the latest-decision card's one line for a
 * no_call (item 073): the label-map phrase, a colon, then the stored
 * detail. The card never shows "confidence —". */
export function noCallLine(decision: {
  no_call_reason: string | null;
  why: string | null;
}): string {
  const reason = decision.no_call_reason ?? 'rules';
  const label = labelFor(NO_CALL_REASONS, reason);
  const detail = noCallDetail(decision.why, reason);
  return detail ? `${label}: ${detail}` : label;
}

function noCallDetail(why: string | null, reason: string): string | null {
  if (!why) return null;
  let detail = why.trim();
  // Older rows prefixed the detail with the reason code itself.
  detail = detail.replace(
    new RegExp(`^${reason.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*[·:]\\s*`, 'i'),
    '',
  );
  detail = detail.replace(/^checks_gate:\s*/, '');
  detail = detail.replace(/\s*—\s*the model was not asked\.?$/i, '');
  detail = detail.trim();
  return detail || null;
}

export function formatAmsTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Amsterdam',
  });
}

/** Hover title: the same moment in New York time. */
export function formatNyTitle(iso: string): string {
  const ny = new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'America/New_York',
  });
  return `New York ${ny}`;
}

export function formatAmsDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Amsterdam',
  });
}

export function formatCost(cost: string | null | undefined): string {
  if (cost === null || cost === undefined) return '—';
  const n = Number(cost);
  if (Number.isNaN(n)) return '—';
  return `$${n.toFixed(5)}`;
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return value.toFixed(2);
}

export function formatNumber(value: unknown, digits = 2): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return value.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

/** Split a validation note into the bold headline and the explanation. */
export function splitNote(note: string): [string, string | null] {
  for (const marker of [' (', ' — ']) {
    const idx = note.indexOf(marker);
    if (idx > 0) {
      const tail = note
        .slice(idx + (marker === ' (' ? 1 : 0))
        .trimStart();
      return [note.slice(0, idx), tail];
    }
  }
  return [note, null];
}
