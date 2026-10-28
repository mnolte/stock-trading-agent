/**
 * Client-side error toasts (docs/SPEC.md → Error pages and API errors).
 *
 * A 500 surfaces as a toast with the server's error id and "Open in
 * Logs"; a 409 says what changed and offers to reload. `client.send`
 * pushes them for every failed call — callers still get the thrown
 * ApiError for their own inline message.
 */
import { useSyncExternalStore } from 'react';

import type { ApiError } from './client';

export interface ApiToast {
  id: number;
  status: number;
  code: string | null;
  message: string;
  errorId: string | null;
}

let toasts: ApiToast[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
// Same error within a few seconds → one toast, not a stack of them.
const recent = new Map<string, number>();
const DEDUPE_MS = 10_000;
const TTL_MS = 15_000;

function emit() {
  listeners.forEach((cb) => cb());
}

export function pushApiToast(error: ApiError): void {
  const key = `${error.status}:${error.code ?? ''}:${error.errorId ?? ''}`;
  const now = Date.now();
  const last = recent.get(key);
  if (last !== undefined && now - last < DEDUPE_MS) return;
  recent.set(key, now);
  const toast: ApiToast = {
    id: nextId++,
    status: error.status,
    code: error.code,
    message: error.message,
    errorId: error.errorId,
  };
  toasts = [...toasts.slice(-2), toast];
  emit();
  setTimeout(() => dismissApiToast(toast.id), TTL_MS);
}

export function dismissApiToast(id: number): void {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function snapshot(): ApiToast[] {
  return toasts;
}

/** Non-hook read for tests. */
export function getApiToasts(): ApiToast[] {
  return toasts;
}

export function useApiToasts(): ApiToast[] {
  return useSyncExternalStore(subscribe, snapshot);
}
