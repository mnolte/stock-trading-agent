import type { StrategyDifference, StrategyRow } from '../types/api';

const DISPLAY_TZ = 'Europe/Amsterdam';

/** Series colours from design/tokens.css: live is the accent, shadows the
 * chart blues/purples, the rules-only baseline the neutral chip colour. */
const SHADOW_COLOURS = ['var(--sa-info)', 'var(--sa-chart-ema50)', 'var(--sa-positive)'];
const SHADOW_HEX = ['#7FB8FF', '#C792EA', '#3DD68C'];

export function rowColours(rows: StrategyRow[]): { css: string; hex: string }[] {
  let shadow = 0;
  return rows.map((row) => {
    if (row.role === 'live') return { css: 'var(--sa-accent)', hex: '#F2B94B' };
    if (row.role === 'baseline') return { css: 'var(--sa-neutral-chip)', hex: '#9FB4CC' };
    const i = shadow % SHADOW_COLOURS.length;
    shadow += 1;
    return { css: SHADOW_COLOURS[i], hex: SHADOW_HEX[i] };
  });
}

export function num(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isNaN(n) ? null : n;
}

/** "−0.49%" — drawdown is shown as a loss; zero stays "0.00%". */
export function drawdown(value: string | null | undefined): string {
  const n = num(value);
  if (n === null) return '—';
  return n === 0 ? '0.00%' : `−${Math.abs(n).toFixed(2)}%`;
}

/** "86% of 212" — "same as live" with the count it is based on. */
export function sameAsLive(pct: string | null, count: number | null): string {
  if (pct === null || count === null || count === 0) return '—';
  return `${Number(pct).toFixed(0)}% of ${count}`;
}

/** "$0.11" — model cost, cents unless below a cent. */
export function cost(value: string | null | undefined): string {
  const n = num(value);
  if (n === null) return '—';
  return `$${n.toFixed(2)}`;
}

/** "5 Oct" in Amsterdam. */
export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    timeZone: DISPLAY_TZ,
  });
}

/** "Wed 7 Oct" for a New York session date (YYYY-MM-DD). */
export function sessionDay(day: string): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  });
}

/** "5 Oct 15:02 · virtual $10,000" — shadow start in Amsterdam time. */
export function startedLine(startedAt: string, equity: string): string {
  const d = new Date(startedAt);
  const time = d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: DISPLAY_TZ,
  });
  const money = Number(equity).toLocaleString('en-US', { maximumFractionDigits: 0 });
  return `${shortDate(startedAt)} ${time} · virtual $${money}`;
}

/** The small-sample warning, with the design's wording. Null once every
 * strategy has enough closed trades to promote. */
export function sampleWarning(
  sessions: number | null,
  rows: StrategyRow[],
  needed: number,
): string | null {
  if (rows.length === 0) return null;
  const counts = rows.map((r) => r.closed);
  const low = Math.min(...counts);
  const high = Math.max(...counts);
  if (low >= needed) return null;
  const sessionText =
    sessions === null ? '' : `${sessions} ${sessions === 1 ? 'session' : 'sessions'} and `;
  const tradeText =
    low === high
      ? `${low} closed ${low === 1 ? 'trade' : 'trades'}`
      : `${low} to ${high} closed trades`;
  return (
    `${sessionText}${tradeText} per strategy: far too few to tell strategies apart. ` +
    `Promoting to live needs at least ${needed} closed trades; read the differences as hints, not results.`
  );
}

const RULE_KEYS = ['rules.rsi_min', 'rules.rsi_max'];
const CHECK_KEYS = [
  'decision.min_confidence_enter',
  'decision.stop_atr_min',
  'decision.stop_atr_max',
  'decision.min_reward_risk',
];

/** "model gpt-4o-mini → gpt-4.1-mini" — every difference on one line. */
export function differsLine(differs: StrategyDifference[]): string {
  if (differs.length === 0) return 'nothing — same as live';
  return differs
    .map((d) =>
      d.key === 'kind'
        ? 'no model: enters whenever the rules pass'
        : `${shortLabel(d)} ${d.live} → ${d.shadow}`,
    )
    .join(' · ');
}

function shortLabel(d: StrategyDifference): string {
  if (d.key === 'decision.prompt') return 'prompt';
  if (d.key === 'decision.model') return 'model';
  if (d.key === 'decision.temperature') return 'temperature';
  return d.label.toLowerCase();
}

/** "prompt decision.v1, temperature 0, rules, checks, risk gates". */
export function sameLine(same: StrategyDifference[]): string {
  const keys = new Set(same.map((s) => s.key));
  const parts: string[] = [];
  for (const s of same) {
    if (['decision.prompt', 'decision.model', 'decision.temperature'].includes(s.key)) {
      parts.push(`${shortLabel(s)} ${s.live}`);
    }
  }
  const allRules = RULE_KEYS.every((k) => keys.has(k));
  const allChecks = CHECK_KEYS.every((k) => keys.has(k));
  if (allRules) parts.push('rules');
  if (allChecks) parts.push('checks');
  for (const s of same) {
    if ((RULE_KEYS.includes(s.key) && !allRules) || (CHECK_KEYS.includes(s.key) && !allChecks)) {
      parts.push(s.label.toLowerCase());
    }
  }
  parts.push('risk gates');
  return parts.join(', ');
}

/** "MSFT 4 sh · CAT 2 sh". */
export function openPositionsLine(positions: { symbol: string; qty: number }[] | undefined): string {
  if (!positions || positions.length === 0) return 'none';
  return positions.map((p) => `${p.symbol} ${p.qty} sh`).join(' · ');
}

/** "14 disagreements in 9 sessions · outcome after 5 sessions or at exit". */
export function disagreementFooter(total: number, sessions: number): string {
  return (
    `${total} ${total === 1 ? 'disagreement' : 'disagreements'} in ${sessions} ` +
    `${sessions === 1 ? 'session' : 'sessions'} · outcome after 5 sessions or at exit`
  );
}
