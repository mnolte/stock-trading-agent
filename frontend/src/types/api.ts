export interface SessionState {
  state: string;
  display_time: string;
  next_cycle: string | null;
  calendar_loaded?: boolean;
}

export interface HeaderState {
  paper: boolean;
  mode: string;
  session: SessionState;
  equity_usd: string | null;
  equity_eur: string | null;
  today_usd: string | null;
  today_eur: string | null;
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
  no_calls: number;
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
  no_call_reason: string | null;
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

export interface BannerItem {
  id: number | null;
  kind: string;
  level: string;
  message: string;
  details?: Record<string, unknown> | null;
  time?: string | null;
  position_id?: number | null;
  automatic?: boolean;
  reason?: string | null;
  since?: string | null;
  actions: string[];
}

export interface BannersResponse {
  items: BannerItem[];
}

export interface PositionRow {
  id: number;
  symbol: string;
  qty: number;
  avg_entry_price: string;
  market_value: string | null;
  current_stop_price: string | null;
  target_price: string | null;
  origin: string;
  pending_ack: boolean;
  deferred_exit: { at: string; reason: string; detail?: string } | null;
  opened_at: string;
  closed_at: string | null;
  realized_pnl_raw: string | null;
  exit_reason: string | null;
}

export interface PositionsResponse {
  open: PositionRow[];
  closed_today: PositionRow[];
  day_trades_5d: number;
}

export interface OrderRow {
  id: number;
  alpaca_order_id: string;
  client_order_id: string | null;
  symbol: string;
  leg: string;
  side: string;
  type: string;
  qty: number;
  limit_price: string | null;
  stop_price: string | null;
  status: string;
  created_at: string;
  filled_at: string | null;
}

export interface OrdersResponse {
  orders: OrderRow[];
}

export interface KillSwitchResponse {
  kill_switch: boolean;
  sold?: string[];
  cancelled_orders?: number;
  market_on_open?: boolean;
  errors?: string[];
}

export interface DecisionDetail {
  id: number;
  cycle_time: string;
  symbol: string;
  context: string;
  final_action: string;
  no_call_reason: string | null;
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
