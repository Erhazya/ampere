// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchHealth, HealthError } from './health';

/** A fake fetch that answers once with this status, body and content type. */
const answering = (status: number, body: string, type = 'application/json') =>
  vi
    .fn<typeof fetch>()
    .mockResolvedValue(new Response(body, { status, headers: { 'Content-Type': type } }));

describe('fetchHealth', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('asks /api/healthz for a fresh JSON answer, passes the signal on, and returns it', async () => {
    const fakeFetch = answering(200, JSON.stringify({ status: 'ok', version: '0.1.0' }));
    vi.stubGlobal('fetch', fakeFetch);
    const { signal } = new AbortController();

    await expect(fetchHealth(signal)).resolves.toEqual({ status: 'ok', version: '0.1.0' });
    const [url, init] = fakeFetch.mock.calls[0];
    expect(url).toBe('/api/healthz');
    expect(init?.headers).toEqual({ Accept: 'application/json' });
    expect(init?.cache).toBe('no-store');
    expect(init?.signal).toBe(signal);
  });

  it('gives up when its signal aborts', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(
        (_url, init) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError')),
            );
          }),
      ),
    );
    const controller = new AbortController();
    const pending = fetchHealth(controller.signal);
    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('reports an error status with its code, as a HealthError', async () => {
    vi.stubGlobal('fetch', answering(503, JSON.stringify({ detail: 'unavailable' })));

    const error: unknown = await fetchHealth().catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(HealthError);
    expect(error).toMatchObject({ failure: { kind: 'http', status: 503 } });
  });

  it('lets a network failure through unchanged', async () => {
    const failure = new TypeError('Failed to fetch');
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockRejectedValue(failure));

    await expect(fetchHealth()).rejects.toBe(failure);
  });

  it('lets a connection lost while reading the body through unchanged', async () => {
    const body = new ReadableStream({
      start(controller) {
        controller.error(new TypeError('terminated'));
      },
    });
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } }),
        ),
    );

    const error: unknown = await fetchHealth().catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(TypeError);
    expect(error).not.toBeInstanceOf(HealthError);
  });

  it('reports an HTML page as an unexpected answer', async () => {
    vi.stubGlobal('fetch', answering(200, '<!doctype html><title>Ampère</title>', 'text/html'));

    await expect(fetchHealth()).rejects.toMatchObject({ failure: { kind: 'format' } });
  });

  it('reports JSON that does not parse as an unexpected answer', async () => {
    vi.stubGlobal('fetch', answering(200, '{"status": "ok"'));

    const error: unknown = await fetchHealth().catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(HealthError);
    expect(error).toMatchObject({ failure: { kind: 'format' } });
  });

  // Each case is wrapped in an array, so that the title shows it (an unwrapped [] would read "undefined").
  it.each([
    [null],
    [[]],
    [{ status: 'down', version: '0.1.0' }],
    [{ status: 'ok', version: 1 }],
    [{ status: 'ok' }],
  ])('reports %j as an unexpected answer', async (body) => {
    vi.stubGlobal('fetch', answering(200, JSON.stringify(body)));

    await expect(fetchHealth()).rejects.toMatchObject({ failure: { kind: 'format' } });
  });
});
