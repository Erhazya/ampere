// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchHealth } from './health';

/** A fake fetch that answers once with this status and JSON body. */
const answering = (status: number, body: unknown) =>
  vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  );

describe('fetchHealth', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('asks /api/healthz for JSON and returns the answer', async () => {
    const fakeFetch = answering(200, { status: 'ok', version: '0.1.0' });
    vi.stubGlobal('fetch', fakeFetch);

    await expect(fetchHealth()).resolves.toEqual({ status: 'ok', version: '0.1.0' });
    const [url, init] = fakeFetch.mock.calls[0];
    expect(url).toBe('/api/healthz');
    expect(init?.headers).toEqual({ Accept: 'application/json' });
  });

  it('rejects an error status', async () => {
    vi.stubGlobal('fetch', answering(503, { detail: 'unavailable' }));

    await expect(fetchHealth()).rejects.toThrow('status 503');
  });

  it('rejects an answer of the wrong shape', async () => {
    vi.stubGlobal('fetch', answering(200, { status: 'ok' }));

    await expect(fetchHealth()).rejects.toThrow('expected shape');
  });
});
