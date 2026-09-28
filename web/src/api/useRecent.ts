import { useCallback, useEffect, useState } from 'react';
import { RECENT_TIMEOUT_MS } from './config';
import { fetchRecent, type Recent, RecentError, type RecentFailure } from './recent';

/** What the Data screen knows about the recent days at a given moment. */
export type RecentState =
  | { state: 'loading' }
  | { state: 'ready'; recent: Recent }
  | { state: 'failed'; failure: RecentFailure };

/** Names the cause of a failed request. */
function classify(error: unknown, timedOut: boolean): RecentFailure {
  if (timedOut) return { kind: 'timeout' };
  if (error instanceof RecentError) return error.failure;
  // fetch rejects with a TypeError when the network fails.
  if (error instanceof TypeError) return { kind: 'network' };
  return { kind: 'unexpected' };
}

/**
 * Asks the API for the recent days once, when the screen shows, and again when `retry` is called
 * after a failure. The export changes once a day, after the daily job: a new visit asks again,
 * and the browser downloads it only if it changed. An answer slower than RECENT_TIMEOUT_MS counts
 * as a failure; on unmount, the request is cancelled and nothing more is reported.
 */
export function useRecent(): [RecentState, () => void] {
  const [recent, setRecent] = useState<RecentState>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let ignore = false;
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, RECENT_TIMEOUT_MS);
    fetchRecent(controller.signal)
      .then((data) => {
        if (!ignore) setRecent({ state: 'ready', recent: data });
      })
      .catch((error: unknown) => {
        if (ignore) return;
        const failure = classify(error, timedOut);
        // Keep the error itself, with its cause and stack, in the browser's console.
        if (failure.kind !== 'none') console.warn('The recent days did not load', error);
        setRecent({ state: 'failed', failure });
      })
      .finally(() => {
        clearTimeout(timer);
      });
    return () => {
      ignore = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [attempt]);

  const retry = useCallback(() => {
    setRecent({ state: 'loading' });
    setAttempt((count) => count + 1);
  }, []);
  return [recent, retry];
}
