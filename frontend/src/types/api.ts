export interface SessionState {
  state: string;
  display_time: string;
  now?: string;
  next_cycle: string | null;
  next_check?: string | null;
  next_open?: string | null;
  next_close?: string | null;
  cycles_today?: number;
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
  warming_up?: boolean;
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
  price_1h_at: string | null;
  price_1session: string | null;
  price_1session_at: string | null;
  price_5sessions: string | null;
  price_5sessions_at: string | null;
  best_price: string | null;
  best_price_at: string | null;
  worst_price: string | null;
  worst_price_at: string | null;
  first_touch: 'stop' | 'target' | 'neither' | null;
  r_1h: string | null;
  r_1session: string | null;
  r_5sessions: string | null;
  best_r: string | null;
  worst_r: string | null;
}

/** The levels a decision traded at: position, order legs or proposed. */
export interface DecisionTrade {
  qty: number | null;
  entry: string | null;
  stop: string | null;
  target: string | null;
  status: string;
}

/** Earnings chip data: next report date + trading sessions seen at decision time. */
export interface DecisionEarnings {
  date: string | null;
  sessions: number | null;
  unavailable: boolean;
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

export interface OrderFill {
  qty: number;
  price: string;
  slippage: string | null;
  filled_at: string;
}

export interface OrderRow {
  id: number;
  alpaca_order_id: string;
  client_order_id: string | null;
  parent_alpaca_order_id: string | null;
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
  filled_avg_price: string | null;
  fills: OrderFill[];
  label?: string;
  replaced_from?: string;
}

export interface OrdersResponse {
  orders: OrderRow[];
}

export interface PositionLeg extends OrderRow {
  label: string;
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
  legs?: PositionLeg[];
  last_price?: string | null;
  unrealized_pnl?: string | null;
  unrealized_pnl_eur?: string | null;
  r_multiple?: string | null;
  stop_distance_label?: string;
  earnings_date?: string | null;
  opened_today?: boolean;
}

export interface DayTradeDay {
  date: string;
  count: number;
}

export interface AccountSummary {
  equity: string | null;
  cash: string | null;
  positions_value: string | null;
  equity_eur: string | null;
  eur_usd_rate: string | null;
  eur_usd_date: string | null;
  margin: string;
  account_number: string | null;
}

export interface ReconciliationSummary {
  ok: boolean;
  checked_at: string | null;
  stream_connected: boolean | null;
  account_number: string | null;
}

export interface PositionsResponse {
  open: PositionRow[];
  closed_today: PositionRow[];
  day_trades_5d: number;
  day_trades?: { days: DayTradeDay[]; count: number; max: number };
  account?: AccountSummary;
  reconciliation?: ReconciliationSummary;
  max_open_positions?: number;
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
  trade: DecisionTrade | null;
  outcome: DecisionOutcome | null;
  next_outcome_due: string | null;
  earnings: DecisionEarnings | null;
  execution_mode: string | null;
  eur_usd_rate: string | null;
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
  orders?: OrderRow[];
  approval?: DecisionApproval | null;
}

export interface DecisionApproval {
  id: number;
  status: ApprovalStatus;
  reason: string | null;
  qty: number;
  limit_price: string;
  created_at: string;
  resolved_at: string | null;
}

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'expired';

export interface Approval {
  id: number;
  decision_id: number;
  symbol: string;
  qty: number;
  limit_price: string;
  stop_price: string;
  target_price: string;
  status: ApprovalStatus;
  reason: string | null;
  created_at: string;
  resolved_at: string | null;
  confidence: number | null;
  action: string | null;
  thesis: string | null;
}

export interface ApprovalsResponse {
  mode: string;
  kill_switch_on: boolean;
  items: Approval[];
  recent: Approval[];
}

export interface ApproveResult {
  ok?: boolean;
  status?: string;
  error?: string;
  [key: string]: unknown;
}

// ---- Settings -------------------------------------------------------------

export interface SettingsForm {
  execution: {
    mode: string;
    entry_limit_offset_pct: number;
    entry_timeout_minutes: number;
    max_spread_pct: number;
  };
  rules: { rsi_min: number; rsi_max: number };
  decision: {
    model: string;
    temperature: number;
    prompt: string;
    min_confidence_enter: number;
    stop_atr_min: number;
    stop_atr_max: number;
    min_reward_risk: number;
    max_calls_per_cycle: number;
  };
  risk: {
    risk_per_trade_pct: number;
    max_position_pct: number;
    max_open_risk_pct: number;
    max_open_positions: number;
    max_per_sector: number;
    daily_loss_limit_pct: number;
    losing_streak: number;
    shock_brake_1h_pct: number;
    shock_brake_session_pct: number;
    earnings_blackout_days: number;
    hold_through_earnings: boolean;
    drawdown_kill_pct: number;
    max_order_pct: number;
    max_orders_per_cycle: number;
  };
  performance: { slippage_pct_per_side: number; starting_equity: number };
  schedule: { cycles: string[] };
  universe: {
    symbols: string[];
    sectors: Record<string, string>;
    allowed_sectors: string[];
  };
}

export interface ScheduleRow {
  index: number;
  ny: string;
  amsterdam: string;
  amsterdam_shifted: string | null;
  bar_used: string;
  entries: string;
  runs_today: boolean | null;
}

export interface ConnectionRow {
  source: string;
  status: string;
  detail: string;
}

export interface SettingsHistoryRow {
  version: number;
  changed_at: string;
  change: string;
  reason: string;
}

export interface SettingsResponse {
  settings: SettingsForm;
  active_version: number;
  pending_version: number | null;
  next_version: number;
  held_symbols: string[];
  prompt_options: { version: string; hash: string }[];
  schedule: {
    rows: ScheduleRow[];
    shifted_label: string | null;
    cycles_per_session: number;
  };
  connections: ConnectionRow[];
  history: SettingsHistoryRow[];
  mode: string;
}

export interface SettingsError {
  detail: Record<string, string> | string;
}

export interface SaveSettingsResult {
  version: number;
  changes: Record<string, { from: unknown; to: unknown }> | string[];
  effective_from: string;
}

// ---- Paper reset + runs ----------------------------------------------------

export interface PaperResetResult {
  ok: boolean;
  message: string;
  closed?: number;
  cancelled?: number;
  errors?: string[];
  [key: string]: unknown;
}

export interface StartRunResult {
  ok: boolean;
  run_id?: number;
  failed?: string[];
  message?: string;
  [key: string]: unknown;
}

export interface Run {
  id: number;
  started_at: string;
  starting_equity: string;
  spy_close: string | null;
}

export interface RunsResponse {
  runs: Run[];
}

// ---- Performance ---------------------------------------------------------

export interface PerformanceRunInfo {
  id: number;
  started_at: string;
  starting_equity: string;
}

export interface PerformanceRun extends PerformanceRunInfo {
  sessions: number;
  current: boolean;
}

export interface PerformanceCurvePoint {
  date: string;
  equity: string;
  benchmark: string | null;
  eur_usd: string | null;
}

export interface PerformanceSummary {
  return_pct: string | null;
  return_usd: string;
  return_eur: string | null;
  spy_return_pct: string | null;
  vs_spy_pct: string | null;
  adjusted_usd: string;
  slippage_pct: string;
  dividends_usd: string;
  win_rate_pct: string | null;
  wins: number;
  closed: number;
  avg_r: string | null;
  max_drawdown_pct: string;
  kill_pct: string;
}

export interface PerformanceAggRow {
  label: string;
  trades: number;
  won: number | null;
  pnl: string | null;
  avg_r: string | null;
}

export interface DecisionOutcomeRow {
  key: string;
  label: string;
  count: number;
  avg_1session_pct: string | null;
  outcomes: number;
  read: string;
}

export interface ClosedTradeRow {
  id: number;
  symbol: string;
  qty: number;
  entry: string;
  exit: string | null;
  opened_at: string;
  closed_at: string;
  pnl: string | null;
  adjusted: string | null;
  r: string | null;
  exit_reason: string | null;
  exit_label: string;
  regime: string | null;
  origin: string;
}

export interface PerformanceResponse {
  empty: boolean;
  runs: PerformanceRunInfo[];
  run?: PerformanceRun;
  summary?: PerformanceSummary;
  curve?: {
    benchmark: string;
    start_label: string;
    points: PerformanceCurvePoint[];
  };
  by_regime?: PerformanceAggRow[];
  by_symbol?: PerformanceAggRow[];
  decision_outcomes?: DecisionOutcomeRow[];
  closed_trades?: ClosedTradeRow[];
  open?: { count: number; symbols: string[]; unrealized: string };
}

// ---- Dashboard + chart -----------------------------------------------------

export interface DashboardCycleLine {
  label: string;
  status: string;
  text: string;
  cycle_index: number;
  cycles_today: number;
}

export interface DashboardStats {
  open_risk_pct: string | null;
  open_risk_usd: string | null;
  open_risk_cap_pct: number;
  positions: number;
  positions_cap: number;
  day_trades: number;
  day_trades_max: number;
  cash: string | null;
  since_start_pct: string | null;
  since_label: string;
  spy_pct: string | null;
  execution_mode: string;
}

export interface DashboardSigChip {
  text: string;
  tone: number;
}

export interface DashboardBoxRow {
  label: string;
  value: string;
  tone: number;
}

export interface DashboardLatestDecision {
  id: number;
  symbol: string;
  context: string;
  model_action: string | null;
  final_action: string;
  no_call_reason: string | null;
  confidence: number | null;
  why: string | null;
  model: string | null;
  regime: { trend: string; volatility: string } | null;
  earnings: DecisionEarnings | null;
  sig: DashboardSigChip[];
  box: DashboardBoxRow[];
  checks: { name: string; passed: boolean; detail: string }[];
  price: string | null;
  time: string;
}

export interface DashboardFeedItem {
  id: number;
  time: string;
  symbol: string;
  context: string;
  action: string;
  model_action: string | null;
  no_call_reason: string | null;
  reason: string | null;
}

export interface MarketClosedCard {
  next_open: string | null;
  first_cycle: string | null;
  stops: { symbol: string; stop: string }[];
  open_count: number;
  today_summary: string;
}

export interface DashboardResponse {
  session: {
    state: string;
    next_cycle: string | null;
    next_open: string | null;
    next_close: string | null;
  };
  cycle_line: DashboardCycleLine | null;
  stats: DashboardStats;
  latest_decision: DashboardLatestDecision | null;
  approvals_pending: number;
  market_closed: MarketClosedCard | null;
  feed: DashboardFeedItem[];
}

export interface ChartBar {
  time: string;
  session: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: number;
  ema20: string | null;
  ema50: string | null;
  vwap: string | null;
}

export interface ChartLevels {
  kind: 'position' | 'order' | 'approval' | null;
  entry?: string | null;
  stop?: string | null;
  target?: string | null;
  opened_at?: string;
}

export interface ChartResponse {
  symbol: string;
  sessions: string[];
  bars: ChartBar[];
  levels: ChartLevels;
  warming_up: boolean;
  price: string | null;
  day_change_pct: string | null;
}
