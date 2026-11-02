/** Fixed suggestions per screen (spec: written in the frontend). */
export const SUGGESTIONS: Record<string, string[]> = {
  dashboard: [
    'What needs me right now?',
    'How did today go?',
    'Why so few entries this week?',
  ],
  decision: [
    'Why did code change this decision?',
    'What happened afterwards?',
    'What did the model want, and what did the gates do?',
  ],
  positions: [
    'How much do I lose if every stop hits?',
    'Which position is closest to its stop?',
    'Why was the JPM stop raised?',
  ],
  strategies: [
    'Is shadow E better than live yet?',
    'Where do the shadows disagree with live?',
    'Which strategy has the best week?',
  ],
  readiness: [
    'What is still missing for live?',
    'Which criterion is furthest from its target?',
    'What changed since last week?',
  ],
  insights: [
    'What is the headline finding?',
    'Which slice loses money?',
    'What should I watch next?',
  ],
  performance: [
    'How am I doing against SPY?',
    'What is my worst trade this month?',
    'Where does the drawdown come from?',
  ],
  reviews: ['Summarise the latest weekly review', 'Which verdicts are waiting?'],
  inspector: ['Why did this call fail?', 'What did the model answer here?'],
  logs: ['Any errors worth reading today?', 'What failed most this week?'],
  settings: ['What is configured differently from the defaults?'],
  backtest: ['How did the last backtest go?'],
  universe: ['Which symbols are eligible and why?'],
  default: ['What needs me right now?', 'How did today go?'],
};

export function suggestionsFor(screen: string): string[] {
  const list = SUGGESTIONS[screen] ?? SUGGESTIONS.default;
  return list.slice(0, 3);
}

/** "decision:2324" / "position:MSFT" / "screen:readiness" → display + link. */
export function refLink(ref: string): { label: string; href: string } {
  const [kind, rest] = ref.split(':', 2);
  const value = rest ?? '';
  switch (kind) {
    case 'decision':
      return { label: `Decision ${value}`, href: `/decisions/${value}` };
    case 'shadow_decision':
      return { label: `Shadow decision ${value}`, href: `/strategies` };
    case 'position':
      return { label: `${value} position`, href: '/positions' };
    case 'trade':
      return { label: `Trade ${value}`, href: '/positions' };
    case 'order':
      return { label: `Order ${value}`, href: '/positions' };
    case 'cycle':
      return { label: `Cycle ${value}`, href: '/' };
    case 'call':
      return { label: `Model call ${value}`, href: `/inspector` };
    case 'calls':
      return { label: `Model calls`, href: `/inspector?${value}` };
    case 'log':
      return { label: `Log line ${value}`, href: '/logs' };
    case 'review':
      return { label: `Review ${value}`, href: '/reviews' };
    case 'note':
      return { label: 'Your note', href: '#' };
    case 'symbol':
      return { label: value, href: '/' };
    case 'screen':
    default:
      return { label: value || ref, href: `/${value === 'dashboard' ? '' : value}` };
  }
}
