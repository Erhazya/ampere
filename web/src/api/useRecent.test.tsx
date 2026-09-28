import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RECENT_TIMEOUT_MS } from './config';
import { useRecent } from './useRecent';

const START = 1_790_028_000_000;

const EXPORT = {
  generated_at: START,
  start: START,
  end: START + 86_400_000,
  series: [],
  days: [],
  sources: [],
};

describe('useRecent', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('loads, then holds the recent days', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(Response.json(EXPORT)));
    const { result } = renderHook(() => useRecent());
    expect(result.current).toEqual({ state: 'loading' });
    await waitFor(() => {
      expect(result.current.state).toBe('ready');
    });
  });

  it('says why the recent days are not there', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          Response.json({ detail: 'No export of the recent days yet' }, { status: 503 }),
        ),
    );
    const { result } = renderHook(() => useRecent());
    await waitFor(() => {
      expect(result.current).toEqual({ state: 'failed', failure: { kind: 'none' } });
    });
  });

  it('names a network failure', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockRejectedValue(new TypeError('offline')));
    const { result } = renderHook(() => useRecent());
    await waitFor(() => {
      expect(result.current).toEqual({ state: 'failed', failure: { kind: 'network' } });
    });
  });

  it('gives up after its timeout', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(
        (_url, init) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () => {
              reject(new DOMException('Aborted', 'AbortError'));
            });
          }),
      ),
    );
    const { result } = renderHook(() => useRecent());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECENT_TIMEOUT_MS);
    });
    expect(result.current).toEqual({ state: 'failed', failure: { kind: 'timeout' } });
  });

  it('cancels its request when the screen goes', () => {
    let aborted = false;
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>((_url, init) => {
        init?.signal?.addEventListener('abort', () => {
          aborted = true;
        });
        return new Promise<Response>(() => {});
      }),
    );
    const { unmount } = renderHook(() => useRecent());
    unmount();
    expect(aborted).toBe(true);
  });
});
