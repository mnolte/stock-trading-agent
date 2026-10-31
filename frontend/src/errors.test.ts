import { afterEach, describe, expect, it, vi } from 'vitest';

import { ErrorBoundary } from './components/ErrorBoundary';
import { chunkReloadPending, installErrorHooks } from './errors';

type Listener = (event: Event) => void;

function fakeBrowser() {
  const listeners: Record<string, Listener[]> = {};
  const store = new Map<string, string>();
  const reload = vi.fn();
  vi.stubGlobal('window', {
    addEventListener: (name: string, fn: Listener) => {
      (listeners[name] ??= []).push(fn);
    },
    location: { reload },
  });
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  });
  const fetchMock = vi
    .fn()
    .mockResolvedValue({ ok: true, json: async () => ({}) });
  globalThis.fetch = fetchMock;
  const fire = (name: string, event: Event) =>
    (listeners[name] ?? []).forEach((fn) => fn(event));
  return { fire, reload, fetchMock };
}

function preloadError(): Event {
  return Object.assign(
    new Event('vite:preloadError', { cancelable: true }),
    { payload: new TypeError('error loading dynamically imported module') },
  );
}

describe('installErrorHooks', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reloads once when a lazy chunk goes stale after a rebuild', () => {
    const { fire, reload, fetchMock } = fakeBrowser();
    installErrorHooks();

    const first = preloadError();
    fire('vite:preloadError', first);
    expect(first.defaultPrevented).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    // keepalive: the reload must not abort the report on its way out.
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/logs/client',
      expect.objectContaining({ keepalive: true }),
    );
  });

  it('lets a repeat failure inside the guard window reach the boundary', () => {
    const { fire, reload } = fakeBrowser();
    installErrorHooks();

    fire('vite:preloadError', preloadError());
    const second = preloadError();
    fire('vite:preloadError', second);

    expect(reload).toHaveBeenCalledTimes(1);
    expect(second.defaultPrevented).toBe(false);
  });

  it('marks the reload pending until the next page load', () => {
    const { fire } = fakeBrowser();
    installErrorHooks();
    expect(chunkReloadPending()).toBe(false);

    fire('vite:preloadError', preloadError());
    expect(chunkReloadPending()).toBe(true);

    // The reload lands: hooks reinstall on the fresh document.
    installErrorHooks();
    expect(chunkReloadPending()).toBe(false);
  });

  it('skips the duplicate boundary report while the reload is pending', () => {
    const { fire, reload, fetchMock } = fakeBrowser();
    installErrorHooks();
    fire('vite:preloadError', preloadError());
    const reports = fetchMock.mock.calls.length;

    // preventDefault resolves the lazy import to undefined; React
    // throws on `.default` before the navigation lands.
    const boundary = new ErrorBoundary({ children: null });
    boundary.componentDidCatch(
      new Error('can\'t access property "default", t._result is undefined'),
    );

    expect(fetchMock.mock.calls.length).toBe(reports);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('reports boundary errors again once the reload landed', () => {
    const { fire, fetchMock } = fakeBrowser();
    installErrorHooks();
    fire('vite:preloadError', preloadError());
    installErrorHooks();

    const boundary = new ErrorBoundary({ children: null });
    boundary.componentDidCatch(new Error('render broke'));

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/logs/client',
      expect.objectContaining({
        body: expect.stringContaining('react boundary: render broke'),
      }),
    );
  });
});
