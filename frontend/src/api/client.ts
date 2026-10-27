const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export class ApiError extends Error {
  status: number;
  detail: unknown;
  /** The PR 25 error code (`not_signed_in`, `confirm_required`, …). */
  code: string | null;

  constructor(status: number, detail: unknown, code: string | null = null) {
    super(typeof detail === 'string' ? detail : `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

async function detailOf(response: Response): Promise<{ detail: unknown; code: string | null }> {
  try {
    const body = (await response.json()) as {
      detail?: unknown;
      error?: { code?: string; message?: string };
    };
    // PR 25 shape: {"error": {"code", "message"}}
    if (body.error?.message) {
      return { detail: body.error.message, code: body.error.code ?? null };
    }
    return { detail: body.detail ?? `HTTP ${response.status}`, code: null };
  } catch {
    return { detail: `HTTP ${response.status}`, code: null };
  }
}

function csrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)sa_csrf=([^;]*)/);
  return match ? match[1] : null;
}

/** 401 → the sign-in page, coming back to the same page after. */
function redirectToSignIn(expired: boolean): void {
  if (window.location.pathname === '/login') return;
  const next = window.location.pathname + window.location.search;
  const flag = expired ? '&expired=1' : '';
  window.location.assign(`/login?next=${encodeURIComponent(next)}${flag}`);
}

async function send(
  method: string,
  path: string,
  body: unknown | undefined,
  retrying: boolean,
): Promise<Response> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  // Double-submit: writes echo the readable sa_csrf cookie.
  if (method !== 'GET' && method !== 'HEAD') {
    const token = csrfToken();
    if (token) headers['X-CSRF-Token'] = token;
  }
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401) {
    const { code } = await detailOf(response.clone());
    if (code === 'not_signed_in' || code === 'session_expired' || code === null) {
      redirectToSignIn(code === 'session_expired');
    }
  }
  if (response.status === 403 && !retrying) {
    const { code } = await detailOf(response.clone());
    if (code === 'confirm_required') {
      // "Confirm it is you" — then repeat the request once (spec).
      const { requestConfirmation } = await import('./auth');
      const ok = await requestConfirmation(actionName(path));
      if (ok) return send(method, path, body, true);
    }
  }
  return response;
}

/** "Stop & flatten" style names for the confirm dialog. */
function actionName(path: string): string {
  if (path === '/api/kill-switch') return 'Stop & flatten';
  if (path === '/api/kill-switch/resume') return 'Resume after the kill switch';
  if (path === '/api/paper-reset') return 'Reset paper account';
  if (path === '/api/runs') return 'Start new run';
  if (path === '/api/settings') return 'Promote strategy';
  if (path.startsWith('/api/keys/')) return 'Replacing a key';
  return 'This action';
}

export async function get<T>(path: string): Promise<T> {
  const response = await send('GET', path, undefined, false);
  if (!response.ok) {
    const { detail, code } = await detailOf(response);
    throw new ApiError(response.status, detail, code);
  }
  return response.json() as Promise<T>;
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await send('POST', path, body, false);
  if (!response.ok) {
    const { detail, code } = await detailOf(response);
    throw new ApiError(response.status, detail, code);
  }
  return response.json() as Promise<T>;
}

/** Flatten a FastAPI `detail` (string | list | field map) into messages. */
export function errorMessages(error: unknown): string[] {
  if (error instanceof ApiError) {
    const d = error.detail;
    if (typeof d === 'string') return [d];
    if (Array.isArray(d)) return d.map(String);
    if (d && typeof d === 'object') return Object.values(d).map(String);
  }
  return [error instanceof Error ? error.message : String(error)];
}
