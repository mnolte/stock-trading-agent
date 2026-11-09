import { Component, Fragment } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { chunkReloadPending, reportError } from '../errors';
import {
  cardErrorKey,
  cardErrorReported,
  markCardErrorReported,
} from './cardErrorReports';
import { warningButton } from './errorPageStyles';

const WARNING_ICON = (
  <svg
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    style={{ flex: '0 0 16px', marginTop: 1 }}
  >
    <path d="M12 3l10 18H2z" />
    <line x1="12" y1="10" x2="12" y2="14" />
    <line x1="12" y1="18" x2="12" y2="18" />
  </svg>
);

interface Props {
  /** Stable card name for the report dedupe key and the log message —
   * the card's label or aria-label. */
  name: string;
  children: ReactNode;
}

interface State {
  detail: string | null;
  errorId: string | null;
  reported: boolean;
  retry: number;
}

/** Per-card error boundary (056 → Failed to draw, States.dc.html → Card
 * states): a card that crashes shows "This card couldn't be drawn. The
 * agent isn't affected." with the error id, Reload card and Open in
 * Logs; the rest of the screen keeps working. The error is sent to
 * /api/logs/client once per card and page load. */
export class CardBoundary extends Component<Props, State> {
  state: State = { detail: null, errorId: null, reported: false, retry: 0 };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { detail: `${error.message}\n${error.stack ?? ''}` };
  }

  componentDidCatch(error: Error): void {
    if (chunkReloadPending()) {
      // Same stale-chunk path as the screen boundary: the reload is
      // already on its way, this throw is not a new error.
      this.setState({ reported: true });
      window.location.reload();
      return;
    }
    const key = cardErrorKey(
      window.location.pathname,
      this.props.name,
      error.message,
    );
    if (!cardErrorReported(key)) {
      markCardErrorReported(key);
      void reportError({
        message: `react card boundary (${this.props.name}): ${error.message}`,
        stack: error.stack,
      }).then((id) => {
        if (id !== null) this.setState({ errorId: id });
      });
    }
    this.setState({ reported: true });
  }

  render(): ReactNode {
    const { detail, errorId, retry } = this.state;
    if (detail !== null) {
      return (
        <div
          role="alert"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--sa-space-3)',
          }}
        >
          <div
            style={{
              display: 'flex',
              gap: 8,
              alignItems: 'flex-start',
              color: 'var(--sa-warning-text)',
              fontSize: 'var(--sa-text-md)',
              lineHeight: 1.5,
            }}
          >
            {WARNING_ICON}
            <span>
              This card couldn&rsquo;t be drawn. The agent isn&rsquo;t
              affected.
              {errorId && <> Error {errorId}.</>}
            </span>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button
              type="button"
              style={warningButton}
              onClick={() =>
                this.setState((s) => ({
                  detail: null,
                  errorId: null,
                  reported: false,
                  retry: s.retry + 1,
                }))
              }
            >
              Reload card
            </button>
            <Link
              to={errorId ? `/logs?search=${encodeURIComponent(errorId)}` : '/logs'}
              style={{ ...warningButton, textDecoration: 'none' }}
            >
              Open in Logs
            </Link>
          </div>
        </div>
      );
    }
    return <Fragment key={retry}>{this.props.children}</Fragment>;
  }
}
