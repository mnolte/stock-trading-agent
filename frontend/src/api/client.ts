import { noteBackendAnswer } from './backend';
import { pushApiToast } from './toasts';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export interface ErrorField {
  name: string;
  message: string;
}

export class ApiError extends Error {
  status: number;
  /** The PR 25 error code (`not_signed_in`, `confirm_required`, …). */
  code: string | null;
  /** 422 field messages. */
  fields: ErrorField[];
  /** 500 answers carry the server's error id. */
  errorId: string | null;
  /** Seconds from Retry-After on 429/503. */
  retryAfter: number | null;

  constructor(
    status: number,
    message: string,
    code: string | null = null,
    opts: {
      fields?: ErrorField[];
      errorId?: string | null;
      retryAfter?: number | null;
    } = {},
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.fields = opts.fields ?? [];
    this.errorId = opts.errorId ?? null;
    this.retryAfter = opts.retryAfter ?? null;
  }
}

interface ParsedError {
  message: string;
  code: string | null;
  fields: ErrorField[];
  errorId: string | null;
}

async function detailOf(response: Response): Promise<ParsedError> {
  try {
    const body = (await response.json()) as {
      detail?: unknown;
      error?: {
        code?: string;
        message?: string;
        error_id?: string;
        fields?: { name?: string; message?: string }[];
      };
    };
    // PR 25 shape: {"error": {"code", "message", "error_id"?, "fields"?}}
    if (body.error?.message) {
      return {
        message: body.error.message,
        code: body.error.code ?? null,
        fields: (body.error.fields ?? []).map((f) => ({
          name: f.name ?? '',
          message: f.message ?? '',
        })),
        errorId: body.error.error_id ?? null,
      };
    }
    return {
      message:
        typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`,
      code: null,
      fields: [],
      errorId: null,
    };
  } catch {
    return { message: `HTTP ${response.status}`, code: null, fields: [], errorId: null };
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

function retryAfterSeconds(response: Response): number | null {
  const raw = response.headers.get('Retry-After');
  if (!raw) return null;
  const seconds = Number(raw);
  return Number.isFinite(seconds) ? seconds : null;
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
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    // No answer at all — the backend banner / isn't-answering page.
    noteBackendAnswer(false);
    throw e;
  }
  noteBackendAnswer(response.status !== 503);
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
  if (!response.ok) {
    const parsed = await detailOf(response);
    const error = new ApiError(response.status, parsed.message, parsed.code, {
      fields: parsed.fields,
      errorId: parsed.errorId,
      retryAfter: retryAfterSeconds(response),
    });
    // Spec table: 409 says what changed and offers to reload; 500 is a
    // toast with the error id and "Open in Logs".
    if (response.status === 409) pushApiToast(error);
    if (response.status === 500) pushApiToast(error);
    throw error;
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
  return response.json() as Promise<T>;
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await send('POST', path, body, false);
  return response.json() as Promise<T>;
}

function humanWait(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} seconds`;
  return `${Math.round(seconds / 60)} minutes`;
}

/** Flatten an API failure into displayable messages: field messages on
 * 422, the registry sentence otherwise, with "try again in …" from
 * Retry-After on 429. */
export function errorMessages(error: unknown): string[] {
  if (error instanceof ApiError) {
    const messages =
      error.fields.length > 0
        ? error.fields.map((f) => (f.name ? `${f.name}: ${f.message}` : f.message))
        : [error.message];
    if (error.retryAfter !== null) {
      return messages.map((m) => `${m} Try again in ${humanWait(error.retryAfter!)}.`);
    }
    return messages;
  }
  return [error instanceof Error ? error.message : String(error)];
}
