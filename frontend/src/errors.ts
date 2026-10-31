/** Frontend error reporting (docs/SPEC.md, Error handling).
 *
 * window.onerror, unhandledrejection and the React error boundary send
 * errors to POST /api/logs/client — at most 10 per minute per tab,
 * enforced server-side. Each report carries an error id the boundary
 * can show.
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export function tabId(): string {
  let id = sessionStorage.getItem('sa-tab-id');
  if (!id) {
    id = `t-${Math.random().toString(16).slice(2, 10)}`;
    sessionStorage.setItem('sa-tab-id', id);
  }
  return id;
}

export function newErrorId(): string {
  return `e-${Math.random().toString(16).slice(2, 8)}`;
}

function csrfToken(): string | null {
  const match =
    typeof document !== 'undefined'
      ? document.cookie.match(/(?:^|;\s*)sa_csrf=([^;]*)/)
      : null;
  return match ? match[1] : null;
}

/** Post a frontend error to /api/logs/client and resolve to the error id
 * the server logged — null when the report couldn't be sent (not signed
 * in, app down). The boundary shows that id; spec → Something went wrong. */
export function reportError(report: {
  message: string;
  source?: string;
  lineno?: number;
  colno?: number;
  stack?: string;
  error_id?: string;
}): Promise<string | null> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = csrfToken();
  if (token) headers['X-CSRF-Token'] = token;
  return fetch(`${API_BASE}/api/logs/client`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      ...report,
      url: window.location.href,
      tab_id: tabId(),
    }),
    // keepalive: the preloadError handler reloads right after reporting —
    // without it the navigation aborts the request before it is sent.
    keepalive: true,
  })
    .then(async (response) => {
      if (!response.ok) return null;
      const body = (await response.json().catch(() => ({}))) as {
        error_id?: string;
      };
      return body.error_id ?? report.error_id ?? null;
    })
    .catch(() => null);
}

export function installErrorHooks(): void {
  window.addEventListener('error', (event) => {
    reportError({
      message: event.message || 'window error',
      source: event.filename,
      lineno: event.lineno,
      colno: event.colno,
      stack: event.error instanceof Error ? event.error.stack : undefined,
    });
  });
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    reportError({
      message:
        reason instanceof Error
          ? `unhandled rejection: ${reason.message}`
          : `unhandled rejection: ${String(reason)}`,
      stack: reason instanceof Error ? reason.stack : undefined,
    });
  });
  // A rebuilt GUI replaces the hashed asset files; a tab still holding
  // the old index.html 404s on its lazy chunks and lands on the error
  // boundary. Vite fires vite:preloadError first — reload so the tab
  // picks up the new build; the minute-guard keeps a broken build from
  // looping, and then the error still reaches the boundary.
  window.addEventListener('vite:preloadError', (event) => {
    const last = Number(sessionStorage.getItem('sa-chunk-reload') ?? 0);
    if (Date.now() - last < 60_000) return;
    sessionStorage.setItem('sa-chunk-reload', String(Date.now()));
    event.preventDefault();
    reportError({
      message: `stale chunk after rebuild: ${String(
        (event as Event & { payload?: unknown }).payload ?? 'preload failed',
      )}`,
    });
    window.location.reload();
  });
}
