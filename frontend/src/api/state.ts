import { get, post } from './client';
import type {
  BannersResponse,
  DecisionDetail,
  DecisionsResponse,
  HeaderState,
  KillSwitchResponse,
  OrdersResponse,
  PositionsResponse,
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
