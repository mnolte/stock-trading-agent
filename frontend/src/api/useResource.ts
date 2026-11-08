import { useEffect, useMemo, useSyncExternalStore } from 'react';

import {
  createResourceLoader,
  type FetchFailure,
  type ResourceFetcher,
  type ResourceLoader,
  type ResourceState,
} from './resourceLoader';

export type { FetchFailure } from './resourceLoader';

export interface Resource<T> {
  data: T | null;
  /** Last successful load — stale data keeps this timestamp. */
  updatedAt: Date | null;
  /** True while the first data is still in flight (the skeleton phase or
   * the first 200 ms before it). */
  loading: boolean;
  /** The card's own skeleton should render: false for the first
   * SKELETON_DELAY_MS of a first load, true after that until the data is
   * in, and held for at least SKELETON_MIN_MS once it went true. */
  showSkeleton: boolean;
  /** First load took longer than LOAD_SLOW_MS — show "Still loading · n s". */
  slow: boolean;
  /** Whole seconds waited so far while `slow` (for the "· n s" count). */
  slowSeconds: number;
  /** A refresh of already-shown data is in flight for more than
   * REFRESH_DELAY_MS — rows dim and the card's top bar runs. */
  refreshing: boolean;
  failure: FetchFailure | null;
  reload: () => void;
}

/**
 * One REST resource on a screen: holds the data plus its freshness and the
 * last failure, and owns the loading timing from design/DESIGN.md →
 * Loading (see resourceLoader.ts). `reload` is stable — hand it to
 * useLiveUpdates or a Retry button. Later calls win over in-flight ones.
 */
export function useApiResource<T>(
  fetcher: ResourceFetcher<T>,
  deps: readonly unknown[] = [],
  enabled = true,
): Resource<T> {
  const loader: ResourceLoader<T> = useMemo(() => createResourceLoader(fetcher), []);
  useEffect(() => {
    loader.setFetcher(fetcher);
  });
  const state: ResourceState<T> = useSyncExternalStore(
    loader.subscribe,
    loader.getState,
  );

  useEffect(() => {
    if (enabled) {
      loader.reload();
    } else {
      loader.invalidate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, loader, ...deps]);

  return {
    data: state.data,
    updatedAt: state.updatedAt,
    loading: enabled && state.data === null && state.failure === null,
    showSkeleton: state.showSkeleton,
    slow: state.slow,
    slowSeconds: state.slowSeconds,
    refreshing: state.refreshing,
    failure: state.failure,
    reload: loader.reload,
  };
}
