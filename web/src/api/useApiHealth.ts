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
 * Asks the API now, then REFRESH_MS after each answer, so two checks never overlap. An
 * answer slower than TIMEOUT_MS counts as a failure. While the page is hidden, no new check
 * starts; showing the page again checks at once. On unmount, the pending request is
 * cancelled and nothing more is reported.
 */
export function useApiHealth(): ApiHealth {
  const [health, setHealth] = useState<ApiHealth>({ state: 'checking' });

  useEffect(() => {
    // Set on unmount: a check still running then reports nothing, and no other one starts.
    let ignore = false;
    let running = false;
    let pending: AbortController | undefined;
    let next: ReturnType<typeof setTimeout> | undefined;

    /** Plans the next check, unless the page is hidden: it then waits to be shown again. */
    const schedule = () => {
      if (ignore || document.hidden) return;
      next = setTimeout(() => {
        next = undefined;
        void check();
      }, REFRESH_MS);
    };

    const check = async () => {
      running = true;
      const controller = new AbortController();
      pending = controller;
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, TIMEOUT_MS);
      try {
        const { version } = await fetchHealth(controller.signal);
        if (ignore) return;
        setHealth({ state: 'up', version, checkedAt: new Date() });
      } catch (error) {
        if (ignore) return;
        const failure = classify(error, timedOut);
        // Keep the error itself, with its stack, in the browser's console.
        if (failure.kind !== 'http' && failure.kind !== 'timeout') {
          console.warn('API health check failed', error);
        }
        setHealth({ state: 'down', failure, checkedAt: new Date() });
      } finally {
        clearTimeout(timer);
        running = false;
        schedule();
      }
    };

    // Back on the page after a pause: a fresh answer at once.
    const onVisibilityChange = () => {
      if (!document.hidden && !running && next === undefined) void check();
    };

    document.addEventListener('visibilitychange', onVisibilityChange);
    void check();
    return () => {
      ignore = true;
      clearTimeout(next);
      pending?.abort();
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, []);

  return health;
}
