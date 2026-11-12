import type { DashboardCycleLine } from '../types/api';
import { nyTime } from '../format';

type CycleSummary = NonNullable<DashboardCycleLine['summary']>;

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** The outcome clause after "asked the model:" — held positions first,
 * then what the calls came to (design/screens/Main.dc.html: "both
 * positions held, every candidate skipped"). */
function cycleOutcomes(s: CycleSummary): string[] {
  const out: string[] = [];
  if (s.held === 2) out.push('both positions held');
  else if (s.held === 1) out.push('the open position held');
  else if (s.held > 2) out.push(`${s.held} open positions held`);
  if (s.calls > 0) {
    if (s.skipped === s.calls) out.push('every candidate skipped');
    else if (s.skipped > 0) out.push(`${s.skipped} skipped`);
    if (s.blocked > 0) out.push(`${s.blocked} blocked`);
    if (s.approved > 0) out.push(`${s.approved} awaiting approval`);
  }
  return out;
}

/** The strip's first part (item 073): "18:46 cycle · 12:46 NY · 69
 * symbols checked, 8 passed the pre-filter and 8 asked the model: both
 * positions held, every candidate skipped. Nothing changed." Falls back
 * to the backend's `text` when there is no per-cycle summary (the
 * market-closed line). */
export function cycleSentence(line: DashboardCycleLine): string {
  const head = line.time
    ? `${line.label} · ${nyTime(line.time)} NY`
    : line.label;
  const s = line.summary;
  if (!s) {
    return line.text ? `${head} · ${line.text}` : head;
  }
  const checked = `${s.checked} symbol${s.checked === 1 ? '' : 's'} checked`;
  const passed = `${s.passed} passed the pre-filter`;
  const calls = `${s.calls} asked the model`;
  const outcomes = cycleOutcomes(s);
  const detail = outcomes.length ? `: ${outcomes.join(', ')}` : '';
  const changes = s.changes ? ` ${capitalize(s.changes)}.` : '';
  return `${head} · ${checked}, ${passed} and ${calls}${detail}.${changes}`;
}

/** Cost as a number, never a concatenated string — "$0.003", with extra
 * digits only when the value would round to zero. */
export function cycleCost(cost: string): string {
  const n = Number(cost);
  if (Number.isNaN(n)) return '—';
  const digits = n > 0 && n < 0.001 ? 5 : 3;
  return `$${n.toFixed(digits)}`;
}
