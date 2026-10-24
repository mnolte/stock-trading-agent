import type { CSSProperties, ReactNode } from 'react';

import type { Resource } from '../api/useResource';
import { displayTime } from '../format';
import { Banner } from './Banner';

const BAR_WIDTHS = ['70%', '90%', '55%'];

/** Skeleton bars + "Loading…" — shown until the first response settles. */
export function SkeletonLines({ bars = 3 }: { bars?: number }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1 }}>
      {BAR_WIDTHS.slice(0, bars).map((w) => (
        <div
          key={w}
          className="sa-skel"
          aria-hidden="true"
          style={{ height: 12, width: w, borderRadius: 4, background: 'var(--sa-subtle)' }}
        />
      ))}
      <span style={{ fontSize: 12, color: 'var(--sa-text-muted)' }}>Loading…</span>
    </div>
  );
}

function WarningIcon() {
  return (
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
}

const RETRY_BUTTON: CSSProperties = {
  height: 36,
  padding: '0 var(--sa-space-3)',
  borderRadius: 'var(--sa-radius-md)',
  border: '1px solid var(--sa-warning-border)',
  background: 'transparent',
  color: 'var(--sa-warning-text)',
  fontSize: 'var(--sa-text-md)',
  fontWeight: 500,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};

export function FetchErrorText({
  what,
  failure,
  updatedAt,
}: {
  what: string;
  failure: { at: Date; message: string };
  updatedAt: Date | null;
}) {
  return (
    <span
      style={{
        display: 'inline-flex',
        gap: 8,
        alignItems: 'flex-start',
      }}
    >
      <WarningIcon />
      <span>
        Couldn&rsquo;t load {what} at {displayTime(failure.at.toISOString())} (
        {failure.message}).
        {updatedAt ? ` Showing data from ${displayTime(updatedAt.toISOString())}.` : ''}
      </span>
    </span>
  );
}

/**
 * "Couldn't load {what} at {time} ({reason}). Showing data from {time}."
 * plus a Retry button — the per-card error state from
 * design/screens/States.dc.html.
 */
export function FetchError({
  what,
  failure,
  updatedAt,
  onRetry,
}: {
  what: string;
  failure: { at: Date; message: string };
  updatedAt: Date | null;
  onRetry: () => void;
}) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 8,
        alignItems: 'flex-start',
        flexWrap: 'wrap',
        color: 'var(--sa-warning-text)',
        fontSize: 'var(--sa-text-md)',
        lineHeight: 1.5,
      }}
    >
      <span style={{ flex: '1 1 260px' }}>
        <FetchErrorText what={what} failure={failure} updatedAt={updatedAt} />
      </span>
      <button type="button" onClick={onRetry} style={RETRY_BUTTON}>
        Retry
      </button>
    </div>
  );
}

/** The same failure, boxed as a warning banner — for resources that are not
 * cards (stat tiles, the approvals strip). */
export function ErrorBanner({
  what,
  failure,
  updatedAt,
  onRetry,
}: {
  what: string;
  failure: { at: Date; message: string };
  updatedAt: Date | null;
  onRetry: () => void;
}) {
  return (
    <Banner
      variant="warning"
      actions={
        <button type="button" onClick={onRetry} style={RETRY_BUTTON}>
          Retry
        </button>
      }
    >
      <FetchErrorText what={what} failure={failure} updatedAt={updatedAt} />
    </Banner>
  );
}

const STALE: CSSProperties = { opacity: 0.55 };

/**
 * Card body driven by a useApiResource result: skeleton before the first
 * response (never empty-state text), the error note (what failed + when +
 * Retry) on failure, stale data dimmed below the note, `empty` copy only
 * after a successful load.
 */
export function CardBody<T>({
  res,
  what,
  isEmpty,
  empty,
  skeleton,
  children,
}: {
  res: Resource<T>;
  /** Noun used in "Couldn't load {what} …". */
  what: string;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  /** Replaces the default text skeleton (e.g. a chart-sized block). */
  skeleton?: ReactNode;
  children: (data: T) => ReactNode;
}) {
  if (res.data === null) {
    if (res.failure) {
      return <FetchError what={what} failure={res.failure} updatedAt={null} onRetry={res.reload} />;
    }
    return <>{skeleton ?? <SkeletonLines />}</>;
  }
  return (
    <>
      {res.failure && (
        <FetchError what={what} failure={res.failure} updatedAt={res.updatedAt} onRetry={res.reload} />
      )}
      {isEmpty && isEmpty(res.data) ? (
        empty
      ) : (
        <div style={res.failure ? STALE : undefined}>{children(res.data)}</div>
      )}
    </>
  );
}
