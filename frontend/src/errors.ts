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

export function reportError(report: {
  message: string;
  source?: string;
  lineno?: number;
  colno?: number;
  stack?: string;
  error_id?: string;
}): void {
  fetch(`${API_BASE}/api/logs/client`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...report,
      url: window.location.href,
      tab_id: tabId(),
    }),
    // A reporting failure must never surface to the user.
  }).catch(() => undefined);
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
}
