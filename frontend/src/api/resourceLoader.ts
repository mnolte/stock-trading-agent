import { ApiError, errorMessages } from './client';
import {
  LOAD_SLOW_MS,
  LOAD_TIMEOUT_MS,
  REFRESH_DELAY_MS,
  SKELETON_DELAY_MS,
  SKELETON_MIN_MS,
} from './timing';

/** A failed fetch: when it happened, what the server/fetch layer said and
 * whether the request never got an answer (backend down or network off). */
export interface FetchFailure {
  at: Date;
  message: string;
  unreachable: boolean;
  /** HTTP status when the server answered (e.g. 404 for record pages). */
  status?: number;
}

export type ResourceFetcher<T> = (signal?: AbortSignal) => Promise<T>;

export interface ResourceState<T> {
  data: T | null;
  updatedAt: Date | null;
  showSkeleton: boolean;
  slow: boolean;
  slowSeconds: number;
  refreshing: boolean;
  failure: FetchFailure | null;
}

export interface ResourceLoader<T> {
  getState(): ResourceState<T>;
  subscribe(cb: () => void): () => void;
  setFetcher(fetcher: ResourceFetcher<T>): void;
  reload(): void;
  /** Invalidate the in-flight load without starting a new one. */
  invalidate(): void;
}

const NO_ANSWER = 'No answer after 20 s';

function initialState<T>(): ResourceState<T> {
  return {
    data: null,
    updatedAt: null,
    showSkeleton: false,
    slow: false,
    slowSeconds: 0,
    refreshing: false,
    failure: null,
  };
}

/**
 * The loading state machine from design/DESIGN.md → Loading, framework-free
 * so it can be unit-tested with fake timers:
 *
 * - first load: nothing for SKELETON_DELAY_MS, then the skeleton; once
 *   shown it stays at least SKELETON_MIN_MS; "Still loading · n s" from
 *   LOAD_SLOW_MS; the request is aborted at LOAD_TIMEOUT_MS into the normal
 *   error state ("No answer after 20 s").
 * - refresh (data already on screen): never a skeleton; `refreshing` goes
 *   true only after REFRESH_DELAY_MS.
 */
export function createResourceLoader<T>(
  fetcher: ResourceFetcher<T>,
): ResourceLoader<T> {
  let state = initialState<T>();
  const listeners = new Set<() => void>();
  let fetcherRef = fetcher;
  let seq = 0;
  let timers: Array<() => void> = [];
  let data: T | null = null;
  let skeletonShownAt: number | null = null;
  let abort: AbortController | null = null;

  const emit = () => listeners.forEach((cb) => cb());
  const patch = (part: Partial<ResourceState<T>>) => {
    state = { ...state, ...part };
    emit();
  };
  const clearTimers = () => {
    timers.forEach((cancel) => cancel());
    timers = [];
  };

  return {
    getState: () => state,
    subscribe(cb) {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    setFetcher(next) {
      fetcherRef = next;
    },
    invalidate() {
      seq += 1;
      abort?.abort();
    },
    reload() {
      seq += 1;
      const loadId = seq;
      const alive = () => loadId === seq;
      clearTimers();
      abort?.abort();
      const controller = new AbortController();
      abort = controller;

      const hadData = data !== null;
      // The load is over once data or a failure is revealed; phase timers
      // check `done` so a late tick can't fail a settled load.
      let done = false;
      const finish = () => {
        done = true;
      };
      const live = () => alive() && !done;

      // `later` scheduled inside this load only fires while it is current.
      const schedule = (ms: number, fn: () => void) => {
        const id = setTimeout(() => {
          if (live()) fn();
        }, ms);
        timers.push(() => clearTimeout(id));
      };
      // The reveal runs after `done` — it only checks the load is current.
      const scheduleReveal = (ms: number, fn: () => void) => {
        const id = setTimeout(() => {
          if (alive()) fn();
        }, ms);
        timers.push(() => clearTimeout(id));
      };
      // Reveal respects the skeleton's minimum on-screen time; `reveal`
      // uses `later`, which is bound to this load through `schedule`.
      const revealNow = (fn: () => void) => {
        const shownAt = skeletonShownAt;
        const wait =
          shownAt === null
            ? 0
            : Math.max(0, SKELETON_MIN_MS - (Date.now() - shownAt));
        if (wait === 0) {
          fn();
        } else {
          scheduleReveal(wait, fn);
        }
      };

      patch({
        showSkeleton: false,
        slow: false,
        slowSeconds: 0,
        refreshing: false,
      });

      if (hadData) {
        schedule(REFRESH_DELAY_MS, () => patch({ refreshing: true }));
      } else {
        schedule(SKELETON_DELAY_MS, () => {
          skeletonShownAt = Date.now();
          patch({ showSkeleton: true });
        });
        schedule(LOAD_SLOW_MS, () => patch({ slow: true }));
        const tick = setInterval(() => {
          if (live()) patch({ slowSeconds: state.slowSeconds + 1 });
        }, 1000);
        timers.push(() => clearInterval(tick));
        // The client stops waiting at LOAD_TIMEOUT_MS whether or not the
        // fetcher honours the abort signal.
        schedule(LOAD_TIMEOUT_MS, () => {
          controller.abort();
          if (!live()) return;
          finish();
          clearTimers();
          revealNow(() => {
            patch({
              failure: {
                at: new Date(),
                message: NO_ANSWER,
                unreachable: true,
              },
              showSkeleton: false,
              slow: false,
              refreshing: false,
            });
          });
          skeletonShownAt = null;
        });
      }

      void fetcherRef(controller.signal)
        .then((result: T) => {
          if (!alive() || done) return;
          finish();
          clearTimers();
          revealNow(() => {
            data = result;
            patch({
              data: result,
              updatedAt: new Date(),
              failure: null,
              showSkeleton: false,
              slow: false,
              refreshing: false,
            });
          });
          skeletonShownAt = null;
        })
        .catch((e: unknown) => {
          if (!alive() || done) return;
          finish();
          clearTimers();
          const timedOut =
            controller.signal.aborted ||
            (e instanceof DOMException && e.name === 'AbortError');
          const failure: FetchFailure = timedOut
            ? { at: new Date(), message: NO_ANSWER, unreachable: true }
            : {
                at: new Date(),
                message: errorMessages(e)[0] ?? 'request failed',
                unreachable: !(e instanceof ApiError),
                status: e instanceof ApiError ? e.status : undefined,
              };
          revealNow(() => {
            patch({
              failure,
              showSkeleton: false,
              slow: false,
              refreshing: false,
            });
          });
          skeletonShownAt = null;
        });
    },
  };
}
