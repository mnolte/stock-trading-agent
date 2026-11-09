import { useState } from 'react';
import type { ReactNode } from 'react';

import type { Resource } from '../api/useResource';
import { displayTime } from '../format';
import { Banner } from './Banner';
import { warningButton } from './errorPageStyles';
import { SkeletonText } from './skeletons';

/** Default skeleton: line bars — replaced per card by the shape of its
 * data (components/skeletons.tsx). */
export function SkeletonLines({ bars = 3 }: { bars?: number }) {
  return <SkeletonText lines={bars} />;
}

/** "Still loading · n s" — the muted line under a skeleton after 8 s. */
function StillLoading({ seconds }: { seconds: number }) {
  return (
    <div
      style={{
        fontSize: 'var(--sa-text-sm)',
        color: 'var(--sa-text-muted)',
        lineHeight: 1.45,
      }}
    >
      Still loading · {seconds} s
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

/** "Alpaca not answering · as of HH:MM" — shown on cards the backend
 * answered from its last reconciled values after a broker read timed
 * out (docs/SPEC.md — Broker reads from the GUI). */
export function AlpacaStaleNote({ asOf }: { asOf: string | null }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        gap: 8,
        alignItems: 'flex-start',
        color: 'var(--sa-warning-text)',
        fontSize: 'var(--sa-text-md)',
        lineHeight: 1.5,
      }}
    >
      <WarningIcon />
      <span>
        Alpaca not answering{asOf ? ` · as of ${displayTime(asOf)}` : ''}
      </span>
    </span>
  );
}

const RETRY_BUTTON = warningButton;

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

/**
 * Card body driven by a useApiResource result (design/DESIGN.md → Loading):
 *
 * - first load: an empty `.sa-loading` body for the first 200 ms, then the
 *   card's own skeleton; "Still loading · n s" under it after 8 s; the
 *   error note (what failed + when + Retry) at 20 s or on any failure —
 *   never empty-state text.
 * - data replaces the skeleton in place with `.sa-in`; nothing below moves.
 * - refresh: rows stay under `.sa-stale-rows` while `res.refreshing` (the
 *   card's `.sa-refresh` bar comes from the Card's `refreshing` prop); a
 *   failed refresh keeps them dimmed under the error note, marked stale.
 * - `empty` copy only after a successful load.
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
  /** The card's own skeleton shape (components/skeletons.tsx); defaults to
   * line bars. Only rendered once `res.showSkeleton` is true. */
  skeleton?: ReactNode;
  children: (data: T) => ReactNode;
}) {
  // Data that replaces a shown skeleton fades in once (`.sa-in`).
  const [hadSkeleton, setHadSkeleton] = useState(false);
  if (res.showSkeleton && !hadSkeleton) setHadSkeleton(true);

  if (res.data === null && !res.loaded) {
    if (res.failure) {
      return <FetchError what={what} failure={res.failure} updatedAt={null} onRetry={res.reload} />;
    }
    return (
      <div className="sa-loading">
        {res.showSkeleton ? (
          <>
            {skeleton ?? <SkeletonLines />}
            {res.slow && <StillLoading seconds={res.slowSeconds} />}
          </>
        ) : null}
      </div>
    );
  }
  if (res.data === null) {
    // The fetcher answered null (e.g. nothing picked yet) — the empty
    // state, never a skeleton.
    return <>{res.failure ? <FetchError what={what} failure={res.failure} updatedAt={res.updatedAt} onRetry={res.reload} /> : empty}</>;
  }
  const rowClass = [
    res.refreshing || res.failure ? 'sa-stale-rows' : '',
    hadSkeleton ? 'sa-in' : '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <>
      {res.failure && (
        <FetchError what={what} failure={res.failure} updatedAt={res.updatedAt} onRetry={res.reload} />
      )}
      {isEmpty && isEmpty(res.data) ? (
        empty
      ) : (
        <div className={rowClass || undefined}>{children(res.data)}</div>
      )}
    </>
  );
}
