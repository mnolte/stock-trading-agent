import { afterEach, describe, expect, it, vi } from 'vitest';

import { installErrorHooks } from './errors';

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
});
