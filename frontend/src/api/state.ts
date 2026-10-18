import { get, post } from './client';
import type {
  ApprovalsResponse,
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
  OrdersResponse,
  PaperResetResult,
  PerformanceResponse,
  PositionsResponse,
  ReviewDetail,
  RiskBudgetResponse,
  ReviewsResponse,
  RunsResponse,
  SaveKeyResult,
  SaveSettingsResult,
  SettingsResponse,
  StartRunResult,
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

export function getDecisions(): Promise<DecisionsResponse> {
  return get<DecisionsResponse>('/api/decisions');
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
): Promise<SaveSettingsResult> {
  return post<SaveSettingsResult>('/api/settings', { settings, reason });
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
