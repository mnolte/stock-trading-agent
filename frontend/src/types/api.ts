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

export interface DecisionSummary {
  calls: number;
  cost: string;
  changed_by_code: number;
  prompt_version: string | null;
  prompt_hash: string | null;
  model: string | null;
  temperature: number | null;
}

export interface DecisionListItem {
  id: number;
  cycle_time: string;
  symbol: string;
  context: string;
  model_action: string | null;
  final_action: string;
  changed_by_code: boolean;
  valid: boolean | null;
}

export interface DecisionsResponse {
  summary: DecisionSummary;
  items: DecisionListItem[];
}

export interface ModelOutput {
  symbol: string;
  action: string;
  confidence: number;
  stop_price: number | null;
  target_price: number | null;
  thesis: string;
  invalidation: string | null;
  risks: string[];
  news_used: string[];
}

export interface CodeCheck {
  name: string;
  passed: boolean;
  detail: string;
  value: string | null;
}

export interface ValidationResult {
  valid: boolean;
  model_action: string | null;
  final_action: string;
  changed_by_code: boolean;
  error: string | null;
  note: string;
  checks: CodeCheck[];
}

export interface DecisionOutcome {
  price_1h: string | null;
  price_1session: string | null;
  price_5sessions: string | null;
  best_price: string | null;
  worst_price: string | null;
}

export interface DecisionDetail {
  id: number;
  cycle_time: string;
  symbol: string;
  context: string;
  final_action: string;
  input_snapshot: Record<string, unknown> | null;
  rules_result: RulesStatus | null;
  model_output: ModelOutput | null;
  validation_result: ValidationResult | null;
  gates_result: Record<string, unknown> | null;
  outcome: DecisionOutcome | null;
  prompt_version: string | null;
  prompt_hash: string | null;
  prompt_text: string | null;
  model: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  tokens: number | null;
  cost: string | null;
  latency_ms: number | null;
  raw_response: string | null;
}
