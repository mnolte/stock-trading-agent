import { get, post } from './client';
import type {
  LlmCallDetail,
  LlmCallsPage,
  LlmCallsSummary,
  ApprovalsResponse,
  BacktestDecisionRow,
  BacktestEstimate,
  BacktestTrade,
  BacktestsResponse,
  ApproveResult,
  BannersResponse,
  ChartResponse,
  DashboardResponse,
  DecisionDetail,
  DecisionsResponse,
  HeaderState,
  KeyGroup,
  KeysResponse,
  KillSwitchResponse,
  NewBacktestBody,
  NewStrategyBody,
  NotificationsResponse,
  OrdersResponse,
  PaperResetResult,
  PerformanceResponse,
  PerformanceTailsResponse,
  PositionsResponse,
  ReadinessResponse,
  ReviewDetail,
  RiskBudgetResponse,
  ReviewsResponse,
  RunsResponse,
  SaveKeyResult,
  SaveSettingsResult,
  SettingsResponse,
  SmtpStatus,
  StartRunResult,
  StrategiesResponse,
  StrategyPromotion,
  TestKeyResult,
  UniverseAddResult,
  UniverseImportResult,
  UniverseResponse,
  VerdictResolved,
  VerdictsResponse,
  VerdictValue,
  WatchlistResponse,
} from '../types/api';

export function getHeaderState(): Promise<HeaderState> {
  return get<HeaderState>('/api/header');
}

export function getWatchlist(): Promise<WatchlistResponse> {
  return get<WatchlistResponse>('/api/watchlist');
}

export function getDecisions(cycle?: string): Promise<DecisionsResponse> {
  const query =
    cycle === undefined ? '' : `?cycle=${encodeURIComponent(cycle)}`;
  return get<DecisionsResponse>(`/api/decisions${query}`);
}

export function getDecision(id: number): Promise<DecisionDetail> {
  return get<DecisionDetail>(`/api/decisions/${id}`);
}

export function getBanners(): Promise<BannersResponse> {
  return get<BannersResponse>('/api/banners');
}

export function getPositions(): Promise<PositionsResponse> {
  return get<PositionsResponse>('/api/positions');
}

export function getOrders(): Promise<OrdersResponse> {
  return get<OrdersResponse>('/api/orders');
}

export function postKillSwitch(action: 'entries' | 'flatten'): Promise<KillSwitchResponse> {
  return post<KillSwitchResponse>('/api/kill-switch', { action });
}

export function postKillSwitchResume(reason: string): Promise<KillSwitchResponse> {
  return post<KillSwitchResponse>('/api/kill-switch/resume', { reason });
}

export function postPause(): Promise<{ paused: boolean }> {
  return post<{ paused: boolean }>('/api/pause', {});
}

export function postPauseResume(): Promise<{ paused: boolean }> {
  return post<{ paused: boolean }>('/api/pause/resume', {});
}

export function postAcknowledgeEvent(id: number): Promise<{ acknowledged: boolean }> {
  return post(`/api/events/${id}/acknowledge`, {});
}

export function postAcknowledgePosition(id: number): Promise<{ acknowledged: boolean }> {
  return post(`/api/positions/${id}/acknowledge`, {});
}

export function getDashboard(symbol?: string): Promise<DashboardResponse> {
  const query = symbol === undefined ? '' : `?symbol=${encodeURIComponent(symbol)}`;
  return get<DashboardResponse>(`/api/dashboard${query}`);
}

export function getChart(symbol: string): Promise<ChartResponse> {
  return get<ChartResponse>(`/api/chart/${symbol}`);
}

export function getRiskBudget(): Promise<RiskBudgetResponse> {
  return get<RiskBudgetResponse>('/api/risk-budget');
}

export function getApprovals(): Promise<ApprovalsResponse> {
  return get<ApprovalsResponse>('/api/approvals');
}

export function postApprove(id: number): Promise<ApproveResult> {
  return post<ApproveResult>(`/api/approvals/${id}/approve`, {});
}

export function postRejectApproval(id: number): Promise<{ rejected: boolean }> {
  return post(`/api/approvals/${id}/reject`, {});
}

export function getSettings(): Promise<SettingsResponse> {
  return get<SettingsResponse>('/api/settings');
}

export function postSettings(
  settings: Record<string, Record<string, unknown>>,
  reason: string,
  promoteStrategyId?: number,
): Promise<SaveSettingsResult> {
  return post<SaveSettingsResult>('/api/settings', {
    settings,
    reason,
    ...(promoteStrategyId !== undefined ? { promote_strategy_id: promoteStrategyId } : {}),
  });
}

export function getNotifications(
  afterId = 0,
  limit = 20,
): Promise<NotificationsResponse> {
  return get<NotificationsResponse>(
    `/api/notifications?after_id=${afterId}&limit=${limit}`,
  );
}

export function postNotificationTest(): Promise<{ ok: boolean }> {
  return post<{ ok: boolean }>('/api/notifications/test', {});
}

export function postSmtpSettings(body: {
  host: string;
  port: number;
  username: string;
  from_addr: string;
  to_addr: string;
  password?: string;
}): Promise<{ ok: boolean; smtp: SmtpStatus }> {
  return post<{ ok: boolean; smtp: SmtpStatus }>(
    '/api/notifications/smtp',
    body,
  );
}

export function getKeys(): Promise<KeysResponse> {
  return get<KeysResponse>('/api/keys');
}

export function postKey(
  group: KeyGroup,
  body: { key_id?: string; secret?: string; value?: string },
): Promise<SaveKeyResult> {
  return post<SaveKeyResult>(`/api/keys/${group}`, body);
}

export function postKeyRemove(group: KeyGroup): Promise<{ ok: boolean; paused_shadows: string[] }> {
  return post<{ ok: boolean; paused_shadows: string[] }>(`/api/keys/${group}/remove`, {});
}

export function postKeyTest(group: KeyGroup): Promise<TestKeyResult> {
  return post<TestKeyResult>(`/api/keys/${group}/test`, {});
}

export function postPaperReset(): Promise<PaperResetResult> {
  return post<PaperResetResult>('/api/paper-reset', {});
}

export function postStartRun(): Promise<StartRunResult> {
  return post<StartRunResult>('/api/runs', {});
}

export function getRuns(): Promise<RunsResponse> {
  return get<RunsResponse>('/api/runs');
}

export function getPerformance(runId?: number): Promise<PerformanceResponse> {
  const query = runId === undefined ? '' : `?run=${runId}`;
  return get<PerformanceResponse>(`/api/performance${query}`);
}

export function getPerformanceTails(runId?: number): Promise<PerformanceTailsResponse> {
  const query = runId === undefined ? '' : `?run=${runId}`;
  return get<PerformanceTailsResponse>(`/api/performance/tails${query}`);
}

export function getReviews(kind: 'weekly' | 'briefing'): Promise<ReviewsResponse> {
  return get<ReviewsResponse>(`/api/reviews?kind=${kind}`);
}

export function getReview(id: number): Promise<ReviewDetail> {
  return get<ReviewDetail>(`/api/reviews/${id}`);
}

export function getVerdicts(status: 'draft' | 'confirmed' | 'all' = 'draft'): Promise<VerdictsResponse> {
  return get<VerdictsResponse>(`/api/verdicts?status=${status}`);
}

export function postVerdict(id: number, verdict: VerdictValue): Promise<VerdictResolved> {
  return post<VerdictResolved>(`/api/verdicts/${id}`, { verdict });
}

export function getUniverse(): Promise<UniverseResponse> {
  return get<UniverseResponse>('/api/universe');
}

export function postUniverseAdd(
  symbol: string,
  sector: string,
): Promise<UniverseAddResult> {
  return post<UniverseAddResult>('/api/universe/pool', { symbol, sector });
}

export function postUniverseImport(csv: string): Promise<UniverseImportResult> {
  return post<UniverseImportResult>('/api/universe/import', { csv });
}

export function postUniverseRemove(
  symbol: string,
): Promise<{ symbol: string; removed: boolean }> {
  return post<{ symbol: string; removed: boolean }>(
    `/api/universe/pool/${encodeURIComponent(symbol)}/remove`,
    {},
  );
}

export function postUniverseScreen(): Promise<Record<string, unknown>> {
  return post<Record<string, unknown>>('/api/universe/screen', {});
}

export function getStrategies(): Promise<StrategiesResponse> {
  return get<StrategiesResponse>('/api/strategies');
}

export function postStrategy(body: NewStrategyBody): Promise<{ id: number }> {
  return post<{ id: number }>('/api/strategies', body);
}

export function postStrategyAction(
  id: number,
  action: 'pause' | 'resume' | 'archive',
): Promise<{ id: number; status: string }> {
  return post<{ id: number; status: string }>(`/api/strategies/${id}/${action}`, {});
}

export function getStrategyPromotion(id: number): Promise<StrategyPromotion> {
  return get<StrategyPromotion>(`/api/strategies/${id}/promotion`);
}

// --- Backtesting (PR 14) ---------------------------------------------------

export function getBacktests(): Promise<BacktestsResponse> {
  return get<BacktestsResponse>('/api/backtests');
}

export function postBacktestEstimate(
  body: NewBacktestBody,
): Promise<BacktestEstimate> {
  return post<BacktestEstimate>('/api/backtests/estimate', body);
}

export function postBacktest(
  body: NewBacktestBody,
): Promise<{ id: number; status: string; reason?: string }> {
  return post<{ id: number; status: string; reason?: string }>(
    '/api/backtests',
    body,
  );
}

export function postBacktestCancel(id: number): Promise<{ ok: boolean }> {
  return post<{ ok: boolean }>(`/api/backtests/${id}/cancel`, {});
}

export async function deleteBacktest(id: number): Promise<{ ok: boolean }> {
  const response = await fetch(`/api/backtests/${id}`, { method: 'DELETE' });
  if (!response.ok) throw new Error(`delete failed: ${response.status}`);
  return (await response.json()) as { ok: boolean };
}

export function postClearBarStore(): Promise<{ ok: boolean }> {
  return post<{ ok: boolean }>('/api/backtests/bar-store/clear', {});
}

export function getBacktestTrades(
  id: number,
  slug: string,
): Promise<{ trades: BacktestTrade[] }> {
  return get<{ trades: BacktestTrade[] }>(
    `/api/backtests/${id}/strategies/${slug}/trades`,
  );
}

export function getBacktestDecisions(
  id: number,
  slug: string,
): Promise<{ items: BacktestDecisionRow[] }> {
  return get<{ items: BacktestDecisionRow[] }>(
    `/api/backtests/${id}/strategies/${slug}/decisions`,
  );
}

export function getBacktestDecision(
  id: number,
  slug: string,
  decisionId: number,
): Promise<DecisionDetail> {
  return get<DecisionDetail>(
    `/api/backtests/${id}/strategies/${slug}/decisions/${decisionId}`,
  );
}

export function getReadiness(): Promise<ReadinessResponse> {
  return get<ReadinessResponse>('/api/readiness');
}

export function postReadinessManual(
  item: string,
  done: boolean,
  note: string,
): Promise<{ saved: boolean }> {
  return post<{ saved: boolean }>('/api/readiness/manual', {
    item,
    done,
    note,
  });
}

export function postDismissEvent(
  id: number,
  note: string,
): Promise<{ dismissed: boolean }> {
  return post<{ dismissed: boolean }>(`/api/events/${id}/dismiss`, { note });
}

export function postDismissUncleanRun(): Promise<{ dismissed: boolean }> {
  return post<{ dismissed: boolean }>('/api/banners/unclean-run/dismiss', {});
}

// --- LLM call history (PR 18) --------------------------------------------

/** Filter dimensions the list, summary and CSV export share. */
export type LlmCallFilters = Record<string, string | undefined>;

function llmCallsQuery(filters: LlmCallFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  const query = params.toString();
  return query ? `?${query}` : '';
}

export function getLlmCalls(
  filters: LlmCallFilters,
  cursor?: string,
  limit = 50,
  order: 'desc' | 'asc' = 'desc',
): Promise<LlmCallsPage> {
  const params = new URLSearchParams(llmCallsQuery(filters));
  params.set('limit', String(limit));
  params.set('order', order);
  if (cursor) params.set('cursor', cursor);
  return get<LlmCallsPage>(`/api/llm-calls?${params}`);
}

export function getLlmCallsSummary(
  filters: LlmCallFilters,
): Promise<LlmCallsSummary> {
  return get<LlmCallsSummary>(`/api/llm-calls/summary${llmCallsQuery(filters)}`);
}

export function getLlmCall(id: number): Promise<LlmCallDetail> {
  return get<LlmCallDetail>(`/api/llm-calls/${id}`);
}

/** The filtered calls' metadata as CSV — never prompts or responses. */
export function llmCallsExportUrl(filters: LlmCallFilters): string {
  return `/api/llm-calls/export.csv${llmCallsQuery(filters)}`;
}
