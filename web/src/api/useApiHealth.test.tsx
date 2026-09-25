import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { REFRESH_MS, TIMEOUT_MS } from './config';
import { fetchHealth, type Health, HealthError } from './health';
import { useApiHealth } from './useApiHealth';

vi.mock('./health', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./health')>()),
  fetchHealth: vi.fn(),
}));
const mockedFetchHealth = vi.mocked(fetchHealth);
const ok: Health = { status: 'ok', version: '0.1.0' };

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
    // Unmount under the fake clock that created the timers, then bring the real one back.
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    mockedFetchHealth.mockReset();
  });

  it('is checking at first, then up with the version of the API', async () => {
    mockedFetchHealth.mockResolvedValue(ok);
    const { result } = renderHook(() => useApiHealth());

    expect(result.current.state).toBe('checking');
    await advance(0);
    expect(result.current).toMatchObject({ state: 'up', version: '0.1.0' });
  });

  it('is down with the HTTP status when the answer has an error status', async () => {
    mockedFetchHealth.mockRejectedValue(new HealthError({ kind: 'http', status: 502 }));
    const { result } = renderHook(() => useApiHealth());

    await advance(0);
    expect(result.current).toMatchObject({ state: 'down', failure: { kind: 'http', status: 502 } });
  });

  it('is down with a network failure, and logs it, when fetch cannot reach the server', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    mockedFetchHealth.mockRejectedValue(new TypeError('Failed to fetch'));
    const { result } = renderHook(() => useApiHealth());

    await advance(0);
    expect(result.current).toMatchObject({ state: 'down', failure: { kind: 'network' } });
    expect(warn).toHaveBeenCalledOnce();
  });

  it('is down with an unexpected failure, and logs it, for any other error', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    mockedFetchHealth.mockRejectedValue(new Error('something else'));
    const { result } = renderHook(() => useApiHealth());

    await advance(0);
    expect(result.current).toMatchObject({ state: 'down', failure: { kind: 'unexpected' } });
    expect(warn).toHaveBeenCalledOnce();
  });

  it('gives up after exactly TIMEOUT_MS', async () => {
    mockedFetchHealth.mockImplementation(hanging);
    const { result } = renderHook(() => useApiHealth());

    await advance(TIMEOUT_MS - 1);
    expect(result.current.state).toBe('checking');
    await advance(1);
    expect(result.current).toMatchObject({ state: 'down', failure: { kind: 'timeout' } });
  });

  it('asks again REFRESH_MS after each answer, and follows the API going down and back', async () => {
    mockedFetchHealth
      .mockResolvedValueOnce(ok)
      .mockRejectedValueOnce(new HealthError({ kind: 'http', status: 502 }))
      .mockResolvedValueOnce(ok);
    const { result } = renderHook(() => useApiHealth());

    await advance(0);
    expect(result.current.state).toBe('up');
    await advance(REFRESH_MS);
    expect(result.current).toMatchObject({ state: 'down', failure: { kind: 'http', status: 502 } });
    await advance(REFRESH_MS);
    expect(result.current.state).toBe('up');
    expect(mockedFetchHealth).toHaveBeenCalledTimes(3);
  });

  it('cancels the pending request on unmount, then stops asking', async () => {
    mockedFetchHealth.mockImplementation(hanging);
    const { unmount } = renderHook(() => useApiHealth());
    await advance(0);
    const signal = mockedFetchHealth.mock.calls[0][0];

    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
    await advance(TIMEOUT_MS + REFRESH_MS);
    expect(mockedFetchHealth).toHaveBeenCalledTimes(1);
  });

  it('stops asking when unmounted between two checks', async () => {
    mockedFetchHealth.mockResolvedValue(ok);
    const { unmount } = renderHook(() => useApiHealth());
    await advance(0);

    unmount();
    await advance(REFRESH_MS);
    expect(mockedFetchHealth).toHaveBeenCalledTimes(1);
  });

  it('ignores the check that StrictMode cancels', async () => {
    mockedFetchHealth.mockImplementation(hanging);
    const { result } = renderHook(() => useApiHealth(), { reactStrictMode: true });

    await advance(0);
    expect(mockedFetchHealth).toHaveBeenCalledTimes(2);
    expect(mockedFetchHealth.mock.calls[0][0]?.aborted).toBe(true);
    expect(result.current.state).toBe('checking');
  });

  it('does not apply a success that arrives for a cancelled check', async () => {
    let answerFirst: (value: Health) => void = () => undefined;
    mockedFetchHealth
      .mockImplementationOnce(
        () =>
          new Promise<Health>((resolve) => {
            answerFirst = resolve;
          }),
      )
      .mockImplementation(hanging);
    const { result } = renderHook(() => useApiHealth(), { reactStrictMode: true });
    await advance(0);

    answerFirst(ok);
    await advance(0);
    expect(result.current.state).toBe('checking');
  });
});
