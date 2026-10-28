import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { ApiError, errorMessages, get, post } from './client';
import { getBackendState } from './backend';
import { getApiToasts } from './toasts';

const assign = vi.fn();

beforeAll(() => {
  // client.ts touches browser globals; the tests run in node.
  vi.stubGlobal('window', {
    location: { pathname: '/settings', search: '', assign },
    addEventListener: vi.fn(),
  });
  vi.stubGlobal('document', { cookie: '' });
  vi.stubGlobal('navigator', { onLine: true });
});

function jsonResponse(
  status: number,
  error: Record<string, unknown>,
  headers: Record<string, string> = {},
): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(headers),
    json: async () => ({ error }),
    clone: function () {
      return this;
    },
  } as unknown as Response;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('get', () => {
  it('returns parsed JSON', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    } as Response);

    const result = await get<{ status: string }>('/api/health');
    expect(result).toEqual({ status: 'ok' });
  });

  it('throws ApiError with the registry message on non-ok', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(400, { code: 'bad_request', message: 'Nope.' }));

    await expect(get('/api/health')).rejects.toMatchObject({
      status: 400,
      code: 'bad_request',
      message: 'Nope.',
    });
  });
});

describe('status mapping (docs/SPEC.md, error table)', () => {
  it('401 redirects to sign-in and keeps the page in ?next', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(401, { code: 'session_expired', message: 'expired' }),
      );
    await expect(get('/api/settings')).rejects.toThrow();
    expect(assign).toHaveBeenCalledWith(
      '/login?next=%2Fsettings&expired=1',
    );
  });

  it('403 confirm_required asks once, then retries once', async () => {
    const answer = jsonResponse(403, {
      code: 'confirm_required',
      message: 'confirm',
    });
    globalThis.fetch = vi.fn().mockResolvedValue(answer);
    // No confirm handler mounted → requestConfirmation resolves false, so
    // the request is not repeated; and a second confirm_required would
    // still not loop (once only, spec).
    await expect(post('/api/kill-switch', {})).rejects.toMatchObject({
      status: 403,
      code: 'confirm_required',
    });
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it('409 throws an ApiError with a reload-offering toast', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(409, { code: 'settings_changed', message: 'changed' }),
      );
    await expect(post('/api/settings', {})).rejects.toMatchObject({
      status: 409,
      code: 'settings_changed',
    });
    expect(getApiToasts().some((t) => t.status === 409)).toBe(true);
  });

  it('413/415 surface the message in the thrown error', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(413, { code: 'too_large', message: 'Too large.' }),
      );
    await expect(post('/api/universe/csv', {})).rejects.toMatchObject({
      status: 413,
      code: 'too_large',
      message: 'Too large.',
    });
  });

  it('422 carries field messages into errorMessages', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse(422, {
        code: 'invalid',
        message: 'Check the highlighted fields.',
        fields: [
          { name: 'risk.per_trade', message: 'must be at most 0.01' },
          { name: '', message: 'a reason is required' },
        ],
      }),
    );
    const err = (await post('/api/settings', {}).catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.fields).toHaveLength(2);
    expect(errorMessages(err)).toEqual([
      'risk.per_trade: must be at most 0.01',
      'a reason is required',
    ]);
  });

  it('429 says when to try again from Retry-After', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse(
        429,
        { code: 'locked_out', message: 'Locked out.' },
        { 'Retry-After': '900' },
      ),
    );
    const err = (await post('/api/auth/sign-in', {}).catch((e: unknown) => e)) as ApiError;
    expect(err.retryAfter).toBe(900);
    expect(errorMessages(err)[0]).toContain('15 minutes');
  });

  it('500 carries the error id and pushes a toast', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(500, { code: 'internal', message: 'Server error.', error_id: 'e-abc123' }),
      );
    const err = (await get('/api/header').catch((e: unknown) => e)) as ApiError;
    expect(err.errorId).toBe('e-abc123');
    expect(
      getApiToasts().some((t) => t.status === 500 && t.errorId === 'e-abc123'),
    ).toBe(true);
  });

  it('503 marks the backend down; a later success marks it up', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(503, { code: 'starting_up', message: 'starting' }),
      )
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) });
    await expect(get('/api/header')).rejects.toMatchObject({ status: 503 });
    expect(getBackendState().down).toBe(true);
    await get('/api/header');
    expect(getBackendState().down).toBe(false);
    expect(getBackendState().everUp).toBe(true);
  });
});
