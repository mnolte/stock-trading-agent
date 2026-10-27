/**
 * Auth state and the "Confirm it is you" plumbing (PR 23).
 *
 * `useAuthStatus` fetches /api/auth/status once — the only open
 * endpoint — and every screen branches on `required`. The confirm flow
 * is requester-agnostic: `client.ts` calls `requestConfirmation()` when
 * a write answers 403 confirm_required, Layout renders the dialog and
 * the original request retries once.
 */
import { useEffect, useState } from 'react';

import { get, post } from './client';

export interface AuthStatus {
  required: boolean;
  has_user: boolean;
  signed_in: boolean;
  confirmed: boolean;
  idle_hours: number;
  confirm_minutes: number;
  instance_id: string;
}

export interface AuthDevice {
  id: number;
  device: string;
  ip: string;
  created_at: string;
  last_seen_at: string;
  keep_signed_in: boolean;
  current: boolean;
}

export interface AuthSessions {
  devices: AuthDevice[];
  failed: {
    count: number;
    addresses: number;
    last_at: string | null;
    last_ip: string | null;
  };
  recovery_codes_left: number;
}

export function useAuthStatus(): AuthStatus | null {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  useEffect(() => {
    let alive = true;
    get<AuthStatus>('/api/auth/status')
      .then((s) => {
        if (alive) setStatus(s);
      })
      .catch(() => {
        /* status is open; a failure here means the API is down */
      });
    return () => {
      alive = false;
    };
  }, []);
  return status;
}

export async function signOut(): Promise<void> {
  await post('/api/auth/sign-out', {});
  window.location.assign('/login');
}

// -------------------------------------------------------------------
// "Confirm it is you": client.ts asks, Layout answers.

export interface ConfirmRequest {
  /** The risky action name, e.g. "Stop & flatten". */
  action: string;
  /** Called with the entered secret; resolves true when it confirmed. */
  resolve: (ok: boolean) => void;
}

type ConfirmHandler = (request: ConfirmRequest) => void;
let confirmHandler: ConfirmHandler | null = null;

export function registerConfirmHandler(handler: ConfirmHandler | null): void {
  confirmHandler = handler;
}

/**
 * Open the confirm dialog and resolve to whether it succeeded. When no
 * handler is mounted (SA_AUTH=off never produces confirm_required) it
 * resolves false.
 */
export function requestConfirmation(action: string): Promise<boolean> {
  return new Promise((resolve) => {
    if (!confirmHandler) {
      resolve(false);
      return;
    }
    confirmHandler({ action, resolve });
  });
}
