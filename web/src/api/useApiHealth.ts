import { useEffect, useState } from 'react';
import { REFRESH_MS, TIMEOUT_MS } from './config';
import { type Failure, fetchHealth, HealthError } from './health';

/** What the dashboard knows about the API at a given moment. */
export type ApiHealth =
  | { state: 'checking' }
  | { state: 'up'; version: string; checkedAt: Date }
  | { state: 'down'; failure: Failure; checkedAt: Date };

/** Names the cause of a failed check. */
function classify(error: unknown, timedOut: boolean): Failure {
  if (timedOut) return { kind: 'timeout' };
  if (error instanceof HealthError) return error.failure;
  // fetch rejects with a TypeError when the network fails.
  if (error instanceof TypeError) return { kind: 'network' };
  return { kind: 'unexpected' };
}

/**
 * Asks the API now, then REFRESH_MS after each answer, so two checks never overlap.
 * An answer slower than TIMEOUT_MS counts as a failure. On unmount, the pending
 * request is cancelled and nothing more is reported.
 */
export function useApiHealth(): ApiHealth {
  const [health, setHealth] = useState<ApiHealth>({ state: 'checking' });

  useEffect(() => {
    let current: AbortController | null = null;
    let next: ReturnType<typeof setTimeout> | undefined;

    const check = async () => {
      const controller = new AbortController();
      current = controller;
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, TIMEOUT_MS);
      try {
        const { version } = await fetchHealth(controller.signal);
        // A check cancelled on unmount reports nothing, success included.
        if (current !== controller) return;
        setHealth({ state: 'up', version, checkedAt: new Date() });
      } catch (error) {
        if (current !== controller) return;
        const failure = classify(error, timedOut);
        // Keep the error itself, with its stack, in the browser's console.
        if (failure.kind !== 'http' && failure.kind !== 'timeout') {
          console.warn('API health check failed', error);
        }
        setHealth({ state: 'down', failure, checkedAt: new Date() });
      } finally {
        clearTimeout(timer);
        if (current === controller) next = setTimeout(() => void check(), REFRESH_MS);
      }
    };

    void check();
    return () => {
      clearTimeout(next);
      const pending = current;
      current = null;
      pending?.abort();
    };
  }, []);

  return health;
}
