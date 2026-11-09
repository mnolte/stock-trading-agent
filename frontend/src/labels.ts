/** One label map for the codes the API returns (056): exit reasons,
 * decision actions, call sources and kinds, and skip/no-call reason
 * codes read as words everywhere — "Agent exit", not "agent_exit". */

function humanize(code: string): string {
  const words = code.replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function labelFor(map: Record<string, string>, code: string | null | undefined): string {
  if (!code) return '—';
  return map[code] ?? humanize(code);
}

export const EXIT_REASONS: Record<string, string> = {
  stop: 'Stop',
  target: 'Target',
  exit: 'Agent exit',
  agent_exit: 'Agent exit',
  earnings_exit: 'Earnings exit',
  stop_breached: 'Stop breached',
  broker_close: 'Broker close',
  forced: 'Forced',
  trailing: 'Trailing stop',
  time_exit: 'Time exit',
  manual: 'Manual',
};

export const ACTIONS: Record<string, string> = {
  enter: 'Enter',
  hold: 'Hold',
  skip: 'Skip',
  exit: 'Exit',
  no_call: 'No call',
  blocked: 'Blocked',
  tighten_stop: 'Tighten stop',
};

export const CALL_SOURCES: Record<string, string> = {
  live: 'Live',
  shadow: 'Shadow',
  review: 'Review',
  ask: 'Ask',
  backtest: 'Backtest',
};

export const CALL_KINDS: Record<string, string> = {
  decision: 'Decision',
  position_check: 'Position check',
  briefing: 'Briefing',
  weekly: 'Weekly review',
  verdict: 'Verdict',
  ask: 'Ask',
  earnings: 'Earnings',
  news: 'News',
  cycle: 'Cycle',
  delta: 'Delta',
  done: 'Done',
  llm: 'LLM',
  rules_only: 'Rules only',
};

export const REASON_CODES: Record<string, string> = {
  call_cap: 'Call cap',
  checks: 'Checks',
  model_error: 'Model error',
  no_options: 'No options',
  rules: 'Rules',
  stop: 'Stop',
  target: 'Target',
  earnings_exit: 'Earnings exit',
  broker_close: 'Broker close',
  stop_breached: 'Stop breached',
  confidence: 'Confidence',
  rsi: 'RSI',
  spread: 'Spread',
  liquidity: 'Liquidity',
};
