import { beforeAll, describe, expect, it, vi } from 'vitest';

import { getChart } from './state';

beforeAll(() => {
  // client.ts touches browser globals; the tests run in node.
  vi.stubGlobal('window', {
    location: { pathname: '/', search: '', assign: vi.fn() },
    addEventListener: vi.fn(),
  });
  vi.stubGlobal('document', { cookie: '' });
  vi.stubGlobal('navigator', { onLine: true });
});

describe('getChart', () => {
  it('makes no request for an empty symbol (30 Oct: GET /api/chart/ 404s)', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    expect(await getChart('')).toBeNull();
    expect(await getChart('   ')).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
