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
  paused: boolean;
  paused_since: string | null;
  resume_blocked?: boolean;
  keys_issue?: string | null;
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
  // Liquidity-screen rank (screened mode only).
  rank?: number | null;
  // Trading days until the next earnings event, for the earnings filter.
  days_to_earnings?: number | null;
}

export interface WatchlistResponse {
  computed_at: string | null;
  regime: {
    trend: string;
    volatility: string;
    label: string;
    blocks_entries: boolean;
  } | null;
  // Which list cycles run — "screened" once a screened screen ran.
  universe_mode?: 'fixed' | 'screened';
  // Rows shown before "Show all" in screened mode.
  watchlist_rows?: number;
  // The earnings filter threshold (trading days).
  earnings_blackout_days?: number;
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
  resume_blocked?: boolean;
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

// ---- Reviews ---------------------------------------------------------------

export type ReviewKind = 'weekly' | 'briefing';

export interface ReviewListItem {
  id: number;
  kind: ReviewKind;
  period: string;
  label: string;
  sub: string;
  source: 'model' | 'template';
  reason: string | null;
  due_at: string | null;
  created_at: string | null;
  model?: string | null;
  prompt_version?: string | null;
  return_pct?: number | null;
  preview?: string | null;
}

export interface ReviewsResponse {
  items: ReviewListItem[];
  next_weekly_due: string | null;
  /** The schedule line under the page title, computed from config. */
  schedule: string;
}

export interface ReviewContent {
  summary?: string;
  observations?: string[];
  suggestion?: string | null;
  text?: string;
}

export interface WeeklyTrade {
  symbol: string;
  opened: string | null;
  closed: string | null;
  exit: string;
  r: number | null;
  pnl_usd: number | null;
  regime: string | null;
}

export interface WeeklySkips {
  total: number;
  with_outcome: number;
  pending: number;
  up: number;
  flat: number;
  down: number;
  biggest_miss: {
    symbol: string;
    date: string;
    confidence: number | null;
    pct: number;
  } | null;
}

export interface WeeklyAggRow {
  name: string;
  trades: number;
  won: number;
  avg_r: number | null;
  pnl_usd: number | null;
}

export interface WeeklyFacts {
  period: {
    week?: number;
    start?: string;
    end?: string;
    due?: string;
    trading_days?: number;
    [key: string]: unknown;
  };
  limits?: {
    weekly_summary_words?: number;
    verdict_after_sessions?: number;
    skip_flat_pct?: number;
    [key: string]: unknown;
  };
  return_pct: number | null;
  return_usd: number | null;
  equity_start: number | null;
  equity_end: number | null;
  spy_return_pct: number | null;
  vs_spy_pts: number | null;
  max_drawdown_pct: number | null;
  trades_closed: number;
  won: number;
  lost: number;
  avg_r: number | null;
  model_cost_usd: number | null;
  model_calls: number;
  reviews_cost_usd: number | null;
  trades: WeeklyTrade[];
  best_trade: WeeklyTrade | null;
  worst_trade: WeeklyTrade | null;
  skips: WeeklySkips;
  verdicts: { good_call: number; bad_call: number; unclear: number; total: number };
  by_regime: WeeklyAggRow[];
  by_symbol: WeeklyAggRow[];
}

export interface BriefingFacts {
  date: string;
  positions: {
    symbol: string;
    qty: number;
    price: number | null;
    stop: number | null;
    stop_distance_pct: number | null;
  }[];
  earnings_today: { symbol: string; time_of_day: string }[];
  earnings_tomorrow: { symbol: string; time_of_day: string }[];
  regime: string;
  yesterday: { return_pct: number | null; return_usd: number | null };
  banners: { kind: string; level: string }[];
}

export interface ReviewDetail extends ReviewListItem {
  facts: Record<string, unknown> | null;
  text: string;
  content: ReviewContent | null;
  model: string | null;
  prompt_version: string | null;
  tokens: number | null;
  cost: string | null;
}

// ---- Verdicts ----------------------------------------------------------------

export type VerdictValue = 'good_call' | 'bad_call' | 'unclear';

export interface VerdictItem {
  id: number;
  decision_id: number;
  symbol: string;
  final_action: string;
  decided_at: string;
  outcome: string | null;
  verdict: VerdictValue;
  reason: string | null;
  source: 'draft' | 'marc';
  draft_source: 'model' | 'template';
  created_at: string | null;
  updated_at: string | null;
}

export interface VerdictsResponse {
  items: VerdictItem[];
  status: string;
}

/** POST /api/verdicts/{id} returns the resolved verdict's core fields. */
export interface VerdictResolved {
  id: number;
  verdict: VerdictValue;
  source: 'draft' | 'marc';
}

export interface DecisionVerdict {
  id: number;
  verdict: VerdictValue;
  reason: string | null;
  source: 'draft' | 'marc';
  draft_source: 'model' | 'template';
  created_at: string | null;
  updated_at: string | null;
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
  verdict: DecisionVerdict | null;
  verdict_eligible: boolean;
  verdict_due: string | null;
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
  paused: boolean;
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
  reviews: { briefing_enabled: boolean; monthly_cost_cap_usd: number };
  readiness: {
    min_closed_trades: number;
    min_sessions: number;
    max_drawdown_pct: number;
    min_avg_r: number;
    min_profit_factor: number;
    ratio_min_trades: number;
    min_proposal_pass_pct: number;
    max_calibration_gap_pts: number;
    min_cycles_completed_pct: number;
    max_price_blocked_pct: number;
    error_auto_resolve_minutes: number;
  };
  schedule: { cycles: string[] };
  notifications: {
    repeat_limit_minutes: number;
    quiet_hours: { start: string; end: string };
    /** event key -> channels (app is always on and not listed) */
    events: Record<string, string[]>;
  };
  universe: {
    symbols: string[];
    sectors: Record<string, string>;
    allowed_sectors: string[];
    mode: string;
    screen: {
      min_price: number;
      min_avg_dollar_volume: number;
      lookback_sessions: number;
      drop_after_failed_screens: number;
    };
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
  /** Saved-but-not-yet-active leaf values, e.g. {'universe.mode': 'screened'}.
   * The form shows the active values; these mark which rows have a pending
   * change waiting for the next cycle. */
  pending?: Record<string, unknown>;
  /** Human-readable diff of pending vs active, e.g. 'Universe mode: fixed →
   * screened'. */
  pending_change?: string | null;
  next_version: number;
  held_symbols: string[];
  priced_models?: string[];
  prompt_options: { version: string; hash: string }[];
  schedule: {
    rows: ScheduleRow[];
    shifted_label: string | null;
    cycles_per_session: number;
  };
  connections: ConnectionRow[];
  history: SettingsHistoryRow[];
  reviews_spend_usd?: string | null;
  /** Event rows for the Notifications section, spec order. */
  notification_events: NotificationEventMeta[];
  /** SMTP card state; null when the notification service is absent. */
  smtp: SmtpStatus | null;
  mode: string;
}

// ---- Notifications ----------------------------------------------------------

export interface NotificationEventMeta {
  key: string;
  label: string;
  description: string;
  group: 'critical' | 'warnings' | 'trading' | 'reviews' | string;
  severity: 'critical' | 'warning' | 'info' | string;
}

export interface NotificationItem {
  id: number;
  time: string;
  event: string;
  severity: string;
  subject: string;
  body: string | null;
  link: string | null;
  channels: string[];
  channel_status: Record<string, string>;
  error: string | null;
}

export interface NotificationsResponse {
  items: NotificationItem[];
  latest_id: number;
}

export interface SmtpStatus {
  configured: boolean;
  host: string;
  port: number;
  username: string;
  from_addr: string;
  to_addr: string;
  password_set: boolean;
  saved_at: string | null;
  tested_at: string | null;
  test_result: string | null;
  test_detail: string | null;
}

export interface SettingsError {
  detail: Record<string, string> | string;
}

// ---- API keys (write-only; never a key value) -------------------------------

export type KeyGroup = 'alpaca' | 'openai' | 'finnhub';
export type KeyStatus = 'ok' | 'failing' | 'untested' | 'missing';

export interface KeyRow {
  name: KeyGroup;
  label: string;
  hint: string;
  set: boolean;
  status: KeyStatus;
  masked: string | null;
  account: string | null;
  saved_at: string | null;
  tested_at: string | null;
  error: string | null;
}

export interface KeysResponse {
  keys: KeyRow[];
  set_count: number;
  all_ready: boolean;
  open_items: number;
  paused_or_kill: boolean;
  replace_allowed: boolean;
  replace_hint: string;
  env_keys: boolean;
  mode: string;
  data_sources: ConnectionRow[];
}

export interface SaveKeyResult {
  ok: boolean;
  account_changed?: boolean;
  key: KeyRow;
}

export interface TestKeyResult {
  ok: boolean;
  error: string | null;
  key: KeyRow;
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

// Cycle counter at the right end of the this-cycle strip. The schedule is
// computed by the backend from the trading calendar and config; the GUI
// only formats it and ticks the countdown (docs/SPEC.md, GUI).

export type CycleSegmentStatus = 'done' | 'next' | 'later';

export interface CycleCounterSegment {
  time: string; // UTC ISO of the scheduled cycle
  status: CycleSegmentStatus;
}

export interface CycleCounter {
  kill_switch: boolean;
  running: boolean;
  now: string | null; // server time, to correct client-clock drift
  next_cycle: string | null;
  next_cycle_today: boolean;
  next_check: string | null;
  next_check_today: boolean;
  segments: CycleCounterSegment[];
  cycle_index: number;
  cycles_today: number;
  run_cycle_number: number | null;
}

export interface DashboardStats {
  positions: number;
  positions_cap: number;
  cash: string | null;
  since_start_pct: string | null;
  since_label: string;
  spy_pct: string | null;
  execution_mode: string;
  // Model call counter (docs/SPEC.md, GUI — Model call counter).
  calls_today: number;
  calls_max: number;
  calls_per_cycle: number;
  cycles_today: number;
  calls_cost_today: string;
  calls_cost_month: string;
  reviews_cost_month: string | null;
  reviews_cost_cap: string;
  shadows_cost_month?: string | null;
  shadows_cost_cap?: string;
}

// ---- Daily briefing card ---------------------------------------------------
// The dashboard card next to the risk budget (docs/SPEC.md, GUI — Daily
// briefing card). The backend builds the chip text; the GUI only renders.

export interface BriefingChip {
  text: string;
  tone: 'neutral' | 'positive' | 'negative' | 'warning';
}

export interface BriefingCardData {
  id: number;
  /** Date label like "Mon 20 Oct" — shown for a previous-day briefing. */
  label: string;
  /** Whether the card covers today's New York session. */
  today: boolean;
  /** The briefing's write slot in app time, "15:00" — shown for today's. */
  time: string;
  text: string;
  chips: BriefingChip[];
  /** "model" = every number checked; "template" = plain fallback + reason. */
  source: 'model' | 'template' | string;
  reason: string | null;
  model: string | null;
  prompt_version: string | null;
  cost: string | null;
}

export interface DashboardBriefing {
  enabled: boolean;
  /** UTC ISO of the next briefing write; null when disabled or unknown. */
  next_at: string | null;
  card: BriefingCardData | null;
}

// ---- Risk budget card ------------------------------------------------------
// Every figure is computed by the backend's risk-gate functions (GET
// /api/risk-budget); the GUI only formats what it is given.

export type BudgetMeterLevel = 'normal' | 'amber' | 'red';

export interface BudgetMeter {
  used_pct: string | null;
  limit_pct: string;
  level: BudgetMeterLevel;
  fill_pct: string; // bar width in % of the limit, capped at 100
}

export interface RiskBudgetResponse {
  settings_version: number | null;
  open_risk: BudgetMeter & { used_usd: string };
  daily_loss: BudgetMeter & { session_pnl_pct: string };
  drawdown: BudgetMeter;
  room: {
    trades: number;
    limited_by: 'positions' | 'open_risk' | 'cash';
  };
  sectors: { sector: string; used: number; cap: number }[];
  risk_per_trade_pct: string;
  losing_streak: { count: number; max: number; factor: string };
  shock_brake: {
    on: boolean;
    limit_1h_pct: string;
    limit_session_pct: string;
    spy_1h_pct: string | null;
    spy_session_pct: string | null;
  };
  day_trades: { count: number; max: number };
  positions: { open: number; cap: number };
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
  cycle_counter: CycleCounter | null;
  stats: DashboardStats;
  briefing: DashboardBriefing | null;
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

// ---- Universe (pool + liquidity screen, v0.2) --------------------------------

export interface UniverseFixedSymbol {
  symbol: string;
  sector: string;
}

export interface UniverseThresholds {
  min_price: number;
  min_avg_dollar_volume: number;
  lookback_sessions: number;
  drop_after_failed_screens: number;
  max_position_pct: number;
}

export interface UniverseFunnelStep {
  key: string;
  count: number;
  out: string[];
}

export interface UniverseScreenSummary {
  session_date: string | null;
  failed: boolean;
  equity?: string | null;
  max_position_usd?: string | null;
  funnel: UniverseFunnelStep[];
  eligible_total: number;
}

export type UniverseRowStatus =
  | 'eligible'
  | 'excluded'
  | 'warming_up'
  | 'held';

export interface UniverseRow {
  symbol: string;
  sector: string;
  added_at: string | null;
  status: UniverseRowStatus;
  rank: number | null;
  last_close: string | null;
  avg_dollar_volume: string | null;
  return_vs_spy: string | null;
  fails: number;
  failed_check: string | null;
  detail: string;
  held: boolean;
  qty: number;
  screened: boolean;
  in_pool: boolean;
}

export interface UniverseCounts {
  pool: number;
  eligible: number;
  excluded: number;
  warming_up: number;
  held: number;
}

export interface UniverseResponse {
  mode: string;
  preview?: boolean;
  allowed_sectors: string[];
  fixed_symbols: UniverseFixedSymbol[];
  held_symbols: string[];
  screen_time_ny: string;
  history: { daily_sessions: number; sessions_5m: number };
  thresholds: UniverseThresholds;
  pool?: { size: number; max: number };
  counts?: UniverseCounts;
  screen?: UniverseScreenSummary | null;
  rows?: UniverseRow[];
}

export interface UniverseAddResult {
  symbol: string;
  sector: string;
  added: boolean;
}

export interface UniverseImportResult {
  added: number;
  skipped: number;
}

// ---- Strategies and shadows (PR 13) ----------------------------------------

export interface StrategyCurvePoint {
  date: string;
  /** % of starting equity, adjusted (slippage per side and dividends). */
  pct: string;
}

export interface StrategyDifference {
  key: string;
  label: string;
  live: string;
  shadow: string;
}

export interface StrategyDisagreement {
  time: string;
  date: string;
  symbol: string;
  live_action: string;
  live_label: string;
  shadow_action: string;
  shadow_label: string;
  live_decision_id: number;
  result: string;
  tone: 'positive' | 'negative' | 'neutral';
}

export interface StrategyRow {
  id: number | null;
  name: string;
  short?: string;
  kind: 'llm' | 'rules_only';
  role: 'live' | 'shadow' | 'baseline';
  status: 'active' | 'paused' | 'archived';
  description: string;
  started_at: string;
  starting_equity: string;
  return_pct: string | null;
  raw_return_pct: string | null;
  vs_spy_pts: string | null;
  closed: number;
  won: number;
  lost: number;
  avg_r: string | null;
  max_drawdown_pct: string | null;
  model_cost: string | null;
  same_as_live_pct: string | null;
  same_as_live_count: number | null;
  curve: StrategyCurvePoint[];
  // Shadows only.
  prompt_version?: string | null;
  model?: string | null;
  temperature?: number | null;
  overrides?: Record<string, number>;
  calls?: number;
  missed?: number;
  open_positions?: { symbol: string; qty: number }[];
  differences?: { differs: StrategyDifference[]; same: StrategyDifference[] };
  promote?: { allowed: boolean; closed: number; needed: number; reason: string | null };
  disagreements?: { total: number; sessions: number; items: StrategyDisagreement[] };
}

export interface StrategyOverrideOption {
  key: string;
  label: string;
  live: number;
  min: number | null;
  max: number | null;
  /** A code check (only applies to model answers). */
  check: boolean;
}

export interface StrategiesResponse {
  synthetic_quote_entries: number;
  run: { id: number; started_at: string; sessions: number | null } | null;
  rows: StrategyRow[];
  spy_curve: StrategyCurvePoint[];
  cost: { month_usd: string; cap_usd: string; cap_reached: boolean };
  slots: { used: number; max: number };
  promote_min_closed_trades: number;
  rules_only: { stop_atr: number; target_r: number };
  options: {
    models: string[];
    prompts: string[];
    live: { prompt_version: string; model: string; temperature: number };
    overrides: StrategyOverrideOption[];
  };
}

export interface NewStrategyBody {
  name: string;
  kind: 'llm' | 'rules_only';
  prompt_version?: string;
  model?: string;
  temperature?: number;
  overrides?: Record<string, number>;
}

export interface StrategyPromotion {
  strategy_id: number;
  name: string;
  allowed: boolean;
  closed: number;
  needed: number;
  changes: Record<string, Record<string, unknown>>;
  lines: string[];
  reason: string;
}

// --- Backtesting (PR 14) ---------------------------------------------------

export type BacktestStatus =
  | 'queued'
  | 'downloading'
  | 'running'
  | 'done'
  | 'cancelled'
  | 'interrupted'
  | 'refused'
  | 'failed';

export interface BacktestStrategyRef {
  ref: string;
  name: string;
  kind: 'llm' | 'rules_only';
  slug: string;
  model?: string | null;
  prompt_version?: string | null;
  temperature?: number | null;
}

export interface BacktestStrategySummary {
  slug: string;
  name: string;
  kind: 'llm' | 'rules_only';
  colour?: string;
  return_pct: number | null;
  vs_spy_pts: number | null;
  spy_return_pct: number | null;
  max_drawdown_pct: number | null;
  closed_trades: number;
  win_rate: number | null;
  avg_r: number | null;
  profit_factor: number | null;
  time_in_market: number | null;
  decisions: number;
  calls: number;
  cache_hits: number;
  cost_usd: number;
  curve: { date: string; equity: number }[];
  spy_curve: { date: string; equity: number }[];
  by_symbol: Record<string, { trades: number; pnl: number }>;
  by_regime: Record<string, { trades: number; avg_r?: number | null; pnl?: number }>;
}

export interface BacktestRun {
  id: number;
  name: string;
  start: string;
  end: string;
  universe_mode: string;
  strategies: BacktestStrategyRef[];
  starting_equity: string;
  slippage_pct: string;
  status: BacktestStatus;
  progress: string | null;
  cost_usd: number;
  process_id: number | null;
  size_bytes: number | null;
  warnings: string[] | null;
  summary: Record<string, BacktestStrategySummary> | null;
  refuse_reason: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface BacktestsResponse {
  runs: BacktestRun[];
  store: { size_bytes: number; earliest: string | null; latest: string | null; month_spent_usd: number };
  monthly_cap: number;
  available: {
    live: { ref: string; name: string; model: string | null; prompt_version: string | null };
    rules_only: { ref: string; name: string };
    shadows: { ref: string; name: string; kind: string; model: string | null; prompt_version: string | null }[];
    priced_models: string[];
    model_cutoffs: Record<string, string>;
    universe: { mode: string; fixed_count: number };
  };
}

export interface BacktestEstimateStrategy {
  ref: string;
  name: string;
  kind: string;
  model: string | null;
  calls: number;
  cache_hits: number;
  cost_usd: number | null;
  cutoff: string | null;
}

export interface BacktestEstimate {
  sessions: number;
  bars_to_download: number;
  download_calls: number;
  download_minutes: number;
  calls: number;
  cache_hits: number;
  cost_usd: number;
  cap_cost_usd: number;
  cap_remaining_usd: number;
  may_stop_early: boolean;
  disabled_reason: string | null;
  cutoff: string | null;
  cutoff_label: string | null;
  strategies: BacktestEstimateStrategy[];
}

export interface NewBacktestBody {
  name?: string;
  start: string;
  end: string;
  universe_mode: string;
  strategies: string[];
  starting_equity: number;
  slippage_pct: number;
}

export interface BacktestTrade {
  id: number;
  symbol: string;
  qty: number;
  entry: string;
  opened_at: string | null;
  closed_at: string | null;
  exit_reason: string | null;
  pnl_raw: string | null;
  pnl_adjusted: string | null;
}

export interface BacktestDecisionRow {
  id: number;
  cycle_time: string | null;
  symbol: string;
  context: string;
  final_action: string;
  no_call_reason: string | null;
  cached: boolean;
}

// ---------------------------------------------------------------------------
// Readiness (docs/SPEC.md, Readiness)

export type ReadinessStatus = 'pass' | 'not_yet' | 'waiting';

export interface ReadinessCriterion {
  key: string;
  group: 'Evidence' | 'Model' | 'Operations' | string;
  label: string;
  value: string;
  target: string;
  /** 0..1 fill of the progress bar; null when the criterion has no bar. */
  progress: number | null;
  status: ReadinessStatus;
  sample: number | null;
  /** Small line under the label (estimate, minimum or missed reason). */
  sub: string | null;
  /** Hover text: how the criterion is measured. */
  hint: string;
  estimate: string | null;
  estimate_date: string | null;
}

export interface ReadinessManualItem {
  item: string;
  label: string;
  detail: string;
  done: boolean;
  note: string | null;
  changed_at: string | null;
}

export interface ReadinessErrorItem {
  id: number;
  time: string;
  level: string;
  event_type: string;
  affects: string;
  message: string;
  resolves_at: string;
}

export interface ReadinessHistoryEntry {
  at: string;
  kind: 'run' | 'criterion' | 'manual' | string;
  change: string;
}

export interface ReadinessResponse {
  run: { id: number; started_at: string } | null;
  snapshot: {
    session_date: string;
    created_at: string;
    criteria: ReadinessCriterion[];
  } | null;
  summary: {
    pass: number;
    not_yet: number;
    waiting: number;
    total: number;
    manual_done: number;
    manual_total: number;
    earliest: { label: string; date: string; text: string } | null;
  };
  manual: ReadinessManualItem[];
  errors: ReadinessErrorItem[];
  history: ReadinessHistoryEntry[];
}
