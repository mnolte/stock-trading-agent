/**
 * Backend reachability (docs/SPEC.md → Error pages and API errors).
 *
 * Every API call reports through `noteBackendAnswer`: a thrown fetch or a
 * 503 marks the backend down; any other answer marks it up. While down a
 * probe hits the open `/api/auth/status` every 15 seconds (or the last
 * Retry-After) until the app answers again.
 *
 * - down and never up yet → the first-load "The app isn't answering" page;
 * - down after a successful load → the backend banner (Interaction and
 *   states), still retrying;
 * - the browser reporting no network → the offline banner instead.
 */
import { useSyncExternalStore } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';
const RETRY_MS = 15_000;

export interface BackendState {
  /** No API answer right now (fetch threw or a 503 arrived). */
  down: boolean;
  /** Browser reports no network. */
  offline: boolean;
  /** When the outage started — the banner names it. */
  since: Date | null;
  /** True once any API answer succeeded in this tab. */
  everUp: boolean;
  /** When the next automatic retry fires. */
  retryAt: Date | null;
}

let state: BackendState = {
  down: false,
  offline: typeof navigator !== 'undefined' ? !navigator.onLine : false,
  since: null,
  everUp: false,
  retryAt: null,
};

const listeners = new Set<() => void>();
let probeTimer: ReturnType<typeof setTimeout> | undefined;
let probing = false;

function emit() {
  listeners.forEach((cb) => cb());
}

function set(patch: Partial<BackendState>) {
  const next = { ...state, ...patch };
  if (
    next.down === state.down &&
    next.offline === state.offline &&
    next.since === state.since &&
    next.everUp === state.everUp &&
    next.retryAt === state.retryAt
  ) {
    return;
  }
  state = next;
  emit();
}

async function probe(): Promise<void> {
  if (probing) return;
  probing = true;
  try {
    const response = await fetch(`${API_BASE}/api/auth/status`, {
      cache: 'no-store',
    });
    if (response.ok) {
      set({ down: false, since: null, everUp: true, retryAt: null });
      stopProbing();
      return;
    }
  } catch {
    // Still unreachable — the next scheduled probe tries again.
  } finally {
    probing = false;
  }
}

function scheduleProbe(ms = RETRY_MS) {
  if (probeTimer !== undefined) return;
  set({ retryAt: new Date(Date.now() + ms) });
  probeTimer = setTimeout(() => {
    probeTimer = undefined;
    void probe();
    if (state.down) scheduleProbe();
  }, ms);
}

function stopProbing() {
  if (probeTimer !== undefined) clearTimeout(probeTimer);
  probeTimer = undefined;
}

function markDown(retryAfter: number | null = null) {
  set({
    down: true,
    since: state.since ?? new Date(),
  });
  scheduleProbe(retryAfter !== null ? retryAfter * 1000 : RETRY_MS);
}

/** Called by client.send for every API answer (or lack of one). */
export function noteBackendAnswer(ok: boolean, retryAfter: number | null = null) {
  if (ok) {
    set({ down: false, since: null, everUp: true, retryAt: null });
    stopProbing();
  } else {
    markDown(retryAfter);
  }
}

/** The "Try now" button and the banner's Retry — probe immediately. */
export function retryNow() {
  void probe();
  scheduleProbe();
}

export function subscribeBackend(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function getBackendState(): BackendState {
  return state;
}

if (typeof window !== 'undefined') {
  window.addEventListener('offline', () => set({ offline: true }));
  window.addEventListener('online', () => {
    set({ offline: false });
    // Back online — probe now rather than waiting out the interval.
    if (state.down) retryNow();
  });
}

export function useBackendStatus(): BackendState {
  return useSyncExternalStore(subscribeBackend, getBackendState);
}
