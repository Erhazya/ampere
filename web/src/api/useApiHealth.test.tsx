import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchHealth } from './health';
import { REFRESH_MS, TIMEOUT_MS, useApiHealth } from './useApiHealth';

vi.mock('./health', () => ({ fetchHealth: vi.fn() }));
const mockedFetchHealth = vi.mocked(fetchHealth);

/** A request that never answers, but gives up when its signal aborts, like fetch. */
const hanging = (signal?: AbortSignal) =>
  new Promise<never>((_, reject) => {
    signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  });

/** Runs the pending promises and the timers due within `ms`, inside act() so React applies the updates. */
const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

describe('useApiHealth', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    mockedFetchHealth.mockReset();
  });

  it('is checking at first, then up with the version of the API', async () => {
    mockedFetchHealth.mockResolvedValue({ status: 'ok', version: '0.1.0' });
    const { result } = renderHook(() => useApiHealth());

    expect(result.current.state).toBe('checking');
    await advance(0);
    expect(result.current).toMatchObject({ state: 'up', version: '0.1.0' });
  });

  it('is down, with the reason, when the API answers with an error', async () => {
    mockedFetchHealth.mockRejectedValue(new Error('The API answered with status 503'));
    const { result } = renderHook(() => useApiHealth());

    await advance(0);
    expect(result.current).toMatchObject({
      state: 'down',
      reason: 'The API answered with status 503',
    });
  });

  it('gives up when the API does not answer in time', async () => {
    mockedFetchHealth.mockImplementation(hanging);
    const { result } = renderHook(() => useApiHealth());

    await advance(TIMEOUT_MS);
    expect(result.current).toMatchObject({ state: 'down', reason: 'No answer within 5 s' });
  });

  it('asks again at every refresh', async () => {
    mockedFetchHealth.mockResolvedValue({ status: 'ok', version: '0.1.0' });
    renderHook(() => useApiHealth());

    await advance(0);
    await advance(REFRESH_MS);
    await advance(REFRESH_MS);
    expect(mockedFetchHealth).toHaveBeenCalledTimes(3);
  });

  it('cancels the pending request on unmount', async () => {
    mockedFetchHealth.mockImplementation(hanging);
    const { unmount } = renderHook(() => useApiHealth());
    await advance(0);
    const signal = mockedFetchHealth.mock.calls[0][0];

    unmount();
    expect(signal?.aborted).toBe(true);
  });
});
