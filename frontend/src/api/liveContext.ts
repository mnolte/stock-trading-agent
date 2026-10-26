import { createContext, useContext, useEffect, useRef } from 'react';

/** WebSocket topics the server pushes, per docs/SPEC.md. */
export type Topic =
  | 'header'
  | 'positions'
  | 'orders'
  | 'decisions'
  | 'banners'
  | 'watchlist'
  | 'performance'
  | 'approvals'
  | 'reviews'
  | 'settings'
  | 'notifications'
  | 'universe'
  | 'strategies'
  | 'backtests'
  | 'readiness'
  | 'logs';

export type LiveStatus = 'live' | 'paused';

export interface Subscription {
  topics: Set<Topic>;
  cb: () => void;
}

export interface LiveContextValue {
  status: LiveStatus;
  subscribe: (topics: Topic[], cb: () => void) => () => void;
}

export const LiveContext = createContext<LiveContextValue>({
  status: 'paused',
  subscribe: () => () => {},
});

export const DEBOUNCE_MS = 300;
export const FALLBACK_POLL_MS = 60_000;
export const RECONNECT_MS = 3_000;

/** Re-run `cb` whenever the server pushes one of `topics` (debounced 300 ms),
 * on reconnect, and every 60 s while the socket is down. */
export function useLiveUpdates(topics: Topic[], cb: () => void): void {
  const { subscribe } = useContext(LiveContext);
  const cbRef = useRef(cb);
  useEffect(() => {
    cbRef.current = cb;
  });
  const key = topics.join(',');
  useEffect(
    () => subscribe(key.split(',') as Topic[], () => cbRef.current()),
    [subscribe, key],
  );
}

/** 'live' | 'paused' — drive the "live updates paused" note. */
export function useLiveStatus(): LiveStatus {
  return useContext(LiveContext).status;
}
