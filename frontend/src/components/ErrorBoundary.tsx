import { Component } from 'react';
import type { ReactNode } from 'react';

import { newErrorId, reportError } from '../errors';

interface Props {
  children: ReactNode;
}

interface State {
  errorId: string | null;
}

/** Last-resort boundary (docs/SPEC.md, Error handling): shows
 * "Something went wrong" with the error_id and a Reload button
 * instead of a blank page; the error is posted to /api/logs/client. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { errorId: null };

  static getDerivedStateFromError(): State {
    return { errorId: newErrorId() };
  }

  componentDidCatch(error: Error): void {
    reportError({
      message: `react boundary: ${error.message}`,
      stack: error.stack,
      error_id: this.state.errorId ?? undefined,
    });
  }

  render(): ReactNode {
    if (this.state.errorId === null) return this.props.children;
    return (
      <div
        role="alert"
        style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 'var(--sa-space-4)',
          color: 'var(--sa-text)',
          fontFamily: 'var(--sa-font-sans)',
          background: 'var(--sa-bg)',
          padding: 'var(--sa-space-6)',
        }}
      >
        <div style={{ fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>
          Something went wrong
        </div>
        <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
          Error id <code style={{ fontFamily: 'var(--sa-font-mono)' }}>{this.state.errorId}</code>
          {' · '}it has been logged. Reload the page to try again.
        </div>
        <button
          type="button"
          onClick={() => window.location.reload()}
          style={{
            height: 'var(--sa-control-height)',
            padding: '0 var(--sa-space-4)',
            borderRadius: 'var(--sa-radius-md)',
            border: '1px solid var(--sa-accent)',
            background: 'var(--sa-accent)',
            color: 'var(--sa-bg)',
            fontSize: 'var(--sa-text-md)',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          Reload
        </button>
      </div>
    );
  }
}
