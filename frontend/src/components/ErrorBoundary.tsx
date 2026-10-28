import { Component } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { reportError } from '../errors';
import { accentButton, ghostButton } from './errorPageStyles';
import { BugIcon } from './errorPages';

interface Props {
  children: ReactNode;
  /** "page" uses the in-app design panel; "app" is the whole-window
   * fallback when Layout itself crashed. */
  variant?: 'page' | 'app';
}

interface State {
  /** The error id returned by /api/logs/client — null while reporting or
   * when the report couldn't be sent. */
  errorId: string | null;
  /** True once the report attempt settled. */
  reported: boolean;
  detail: string | null;
}

/** One boundary per screen (docs/SPEC.md → Something went wrong): the
 * error goes to /api/logs/client and the panel shows the returned error
 * id, "Reload page", "Copy error id" and "Open in Logs". The sidebar and
 * other screens keep working. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { errorId: null, reported: false, detail: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { detail: `${error.message}\n${error.stack ?? ''}` };
  }

  componentDidCatch(error: Error): void {
    void reportError({
      message: `react boundary: ${error.message}`,
      stack: error.stack,
    }).then((id) => this.setState({ errorId: id, reported: true }));
  }

  render(): ReactNode {
    const { errorId, reported, detail } = this.state;
    if (detail === null) return this.props.children;
    const copy = () => {
      const text = errorId ?? `no error id — the report could not be sent`;
      void navigator.clipboard?.writeText(
        errorId ? errorId : `${text}\n${detail}`,
      );
    };
    const panel = (
      <div
        role="alert"
        style={{
          flex: 1,
          minHeight: this.props.variant === 'app' ? '100vh' : undefined,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 'var(--sa-space-6)',
          color: 'var(--sa-text)',
          fontFamily: 'var(--sa-font-sans)',
          background: 'var(--sa-bg)',
        }}
      >
        <div style={{ maxWidth: 440, display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)', alignItems: 'flex-start' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ width: 36, height: 36, borderRadius: 8, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', color: 'var(--sa-negative-text)' }}>
              <BugIcon />
            </span>
            <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              {errorId ? `error ${errorId}` : 'error'}
            </span>
          </div>
          <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>
            Something went wrong on this page
          </h2>
          <p style={{ margin: 0, fontSize: 'var(--sa-text-base)', color: 'var(--sa-text-2)', lineHeight: 1.55 }}>
            {errorId
              ? 'The page hit an error and stopped drawing. The agent itself is not affected and keeps trading. The error was logged with the id above.'
              : 'The page hit an error and stopped drawing. The agent itself is not affected and keeps trading. The error could not be reported — copy the details below.'}
          </p>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            {this.props.variant === 'app'
              ? 'The whole app stopped drawing — reload to start again.'
              : 'Only this screen failed; the sidebar and the other screens still work.'}
          </span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 4 }}>
            <button type="button" style={accentButton} onClick={() => window.location.reload()}>
              Reload page
            </button>
            <button type="button" style={ghostButton} onClick={copy}>
              {errorId ? 'Copy error id' : 'Copy details'}
            </button>
            {errorId && (
              <Link to={`/logs?search=${encodeURIComponent(errorId)}`} style={{ ...ghostButton, textDecoration: 'none' }}>
                Open in Logs
              </Link>
            )}
          </div>
          {!reported && (
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              Reporting the error…
            </span>
          )}
        </div>
      </div>
    );
    return panel;
  }
}
