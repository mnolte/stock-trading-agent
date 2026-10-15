import { describe, expect, it, vi } from 'vitest';

import { get } from './client';

describe('get', () => {
  it('returns parsed JSON', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    } as Response);

    const result = await get<{ status: string }>('/api/health');
    expect(result).toEqual({ status: 'ok' });
  });

  it('throws on non-ok response', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    } as Response);

    await expect(get('/api/health')).rejects.toThrow('HTTP 500');
  });
});
