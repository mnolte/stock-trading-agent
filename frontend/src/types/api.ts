export interface SessionState {
  state: string;
  display_time: string;
  next_cycle: string | null;
}

export interface HeaderState {
  paper: boolean;
  mode: string;
  session: SessionState;
  equity_usd: string;
  equity_eur: string;
  today_usd: string;
  today_eur: string;
  regime: string;
  kill_switch: boolean;
}

export interface RuleCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface RulesStatus {
  status: 'pass' | 'fail' | 'held';
  reason: string | null;
  checks: RuleCheck[];
}

export interface WatchlistRow {
  symbol: string;
  price: string | null;
  day_change_pct: string | null;
  rules: RulesStatus;
  earnings_date: string | null;
  last_action: string | null;
  last_action_time: string | null;
}

export interface WatchlistResponse {
  computed_at: string | null;
  regime: {
    trend: string;
    volatility: string;
    label: string;
    blocks_entries: boolean;
  } | null;
  rows: WatchlistRow[];
}
