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
  KillSwitchResponse,
  OrdersResponse,
  PaperResetResult,
  PerformanceResponse,
  PositionsResponse,
  RunsResponse,
  SaveSettingsResult,
  SettingsForm,
  SettingsResponse,
  StartRunResult,
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

export function postAcknowledgeEvent(id: number): Promise<{ acknowledged: boolean }> {
  return post(`/api/events/${id}/acknowledge`, {});
}

export function postAcknowledgePosition(id: number): Promise<{ acknowledged: boolean }> {
  return post(`/api/positions/${id}/acknowledge`, {});
}

export function getDashboard(): Promise<DashboardResponse> {
  return get<DashboardResponse>('/api/dashboard');
}

export function getChart(symbol: string): Promise<ChartResponse> {
  return get<ChartResponse>(`/api/chart/${symbol}`);
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
  settings: SettingsForm,
  reason: string,
): Promise<SaveSettingsResult> {
  return post<SaveSettingsResult>('/api/settings', { settings, reason });
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
