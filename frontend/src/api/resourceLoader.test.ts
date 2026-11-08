import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createResourceLoader } from './resourceLoader';
import type { ResourceState } from './resourceLoader';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function setup<T>(fetcher: (signal?: AbortSignal) => Promise<T>) {
  const loader = createResourceLoader(fetcher);
  const seen: ResourceState<T>[] = [];
  loader.subscribe(() => seen.push(loader.getState()));
  loader.reload();
  return { loader, seen, state: () => loader.getState() };
}

describe('resourceLoader timing (055)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('answers at 150 ms → no skeleton', async () => {
    const d = deferred<string>();
    const { state } = setup(() => d.promise);
    await vi.advanceTimersByTimeAsync(150);
    d.resolve('ok');
    await vi.advanceTimersByTimeAsync(0);
    expect(state().data).toBe('ok');
    expect(state().showSkeleton).toBe(false);
    expect(state().failure).toBeNull();
  });

  it('answers at 250 ms → skeleton, held until 600 ms', async () => {
    const d = deferred<string>();
    const { state } = setup(() => d.promise);
    await vi.advanceTimersByTimeAsync(100);
    expect(state().showSkeleton).toBe(false);
    await vi.advanceTimersByTimeAsync(150); // t = 250
    expect(state().showSkeleton).toBe(true);
    d.resolve('ok');
    await vi.advanceTimersByTimeAsync(0);
    // Skeleton went true at 200 ms; it stays at least 400 ms → data at 600.
    expect(state().data).toBeNull();
    expect(state().showSkeleton).toBe(true);
    await vi.advanceTimersByTimeAsync(350); // t = 600
    expect(state().data).toBe('ok');
    expect(state().showSkeleton).toBe(false);
  });

  it('slow line at 8 s', async () => {
    const d = deferred<string>();
    const { state } = setup(() => d.promise);
    await vi.advanceTimersByTimeAsync(8_000);
    expect(state().slow).toBe(true);
    expect(state().slowSeconds).toBe(8);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(state().slowSeconds).toBe(10);
    d.resolve('late');
    await vi.advanceTimersByTimeAsync(0);
    expect(state().data).toBe('late');
    expect(state().slow).toBe(false);
  });

  it('aborts the first load at 20 s into the error state', async () => {
    let signal: AbortSignal | undefined;
    const { state } = setup((s) => {
      signal = s;
      return new Promise<string>((_res, rej) => {
        s?.addEventListener('abort', () =>
          rej(new DOMException('The operation was aborted.', 'AbortError')),
        );
      });
    });
    await vi.advanceTimersByTimeAsync(20_000);
    expect(signal?.aborted).toBe(true);
    // The abort rejects the fetch; the failure lands after the reveal.
    await vi.advanceTimersByTimeAsync(0);
    expect(state().failure?.message).toBe('No answer after 20 s');
    expect(state().showSkeleton).toBe(false);
  });

  it('a fetcher that ignores the signal still fails at 20 s', async () => {
    const { state } = setup(() => new Promise<string>(() => {}));
    await vi.advanceTimersByTimeAsync(20_000);
    expect(state().failure?.message).toBe('No answer after 20 s');
    expect(state().data).toBeNull();
  });

  it('refresh keeps rows; refreshing goes true at 400 ms', async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    let calls = 0;
    const { state, loader } = setup(() => (calls++ === 0 ? first.promise : second.promise));
    first.resolve('v1');
    await vi.advanceTimersByTimeAsync(0);
    expect(state().data).toBe('v1');

    loader.reload();
    await vi.advanceTimersByTimeAsync(399);
    expect(state().refreshing).toBe(false);
    expect(state().showSkeleton).toBe(false);
    expect(state().data).toBe('v1');
    await vi.advanceTimersByTimeAsync(1); // t = 400
    expect(state().refreshing).toBe(true);
    second.resolve('v2');
    await vi.advanceTimersByTimeAsync(0);
    expect(state().data).toBe('v2');
    expect(state().refreshing).toBe(false);
  });

  it('a refresh failure keeps the data and sets the failure', async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    let calls = 0;
    const { state, loader } = setup(() => (calls++ === 0 ? first.promise : second.promise));
    first.resolve('v1');
    await vi.advanceTimersByTimeAsync(0);
    loader.reload();
    second.reject(new Error('boom'));
    await vi.advanceTimersByTimeAsync(0);
    expect(state().data).toBe('v1');
    expect(state().failure?.message).toBe('boom');
  });
});
