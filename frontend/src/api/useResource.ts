import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, errorMessages } from './client';
import { createSequence } from './latestWins';

/** A failed fetch: when it happened, what the server/fetch layer said and
 * whether the request never got an answer (backend down or network off). */
export interface FetchFailure {
  at: Date;
  message: string;
  unreachable: boolean;
}

export interface Resource<T> {
  data: T | null;
  /** Last successful load — stale data keeps this timestamp. */
  updatedAt: Date | null;
  /** True until the first response settles: skeletons show, no empty text. */
  loading: boolean;
  failure: FetchFailure | null;
  reload: () => void;
}

/**
 * One REST resource on a screen: holds the data plus its freshness and the
 * last failure. `reload` is stable — hand it to useLiveUpdates or a Retry
 * button. Later calls win over in-flight ones.
 */
export function useApiResource<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[] = [],
  enabled = true,
): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [settled, setSettled] = useState(false);
  const seq = useRef(createSequence());
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const reload = useCallback(() => {
    const isCurrent = seq.current.next();
    void fetcherRef.current()
      .then((result: T) => {
        if (!isCurrent()) return;
        setData(result);
        setUpdatedAt(new Date());
        setFailure(null);
        setSettled(true);
      })
      .catch((e: unknown) => {
        if (!isCurrent()) return;
        setSettled(true);
        setFailure({
          at: new Date(),
          message: errorMessages(e)[0] ?? 'request failed',
          unreachable: !(e instanceof ApiError),
        });
      });
  }, []);

  useEffect(() => {
    if (enabled) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, reload, ...deps]);

  return {
    data,
    updatedAt,
    loading: enabled && !settled && failure === null,
    failure,
    reload,
  };
}
