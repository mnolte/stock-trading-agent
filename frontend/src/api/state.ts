import { get } from './client';
import type {
  DecisionDetail,
  DecisionsResponse,
  HeaderState,
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
