import { useEffect, useState } from 'react';
import { fetchHealth } from './health';

/** What the dashboard knows about the API at a given moment. */
export type ApiHealth =
  | { state: 'checking' }
  | { state: 'up'; version: string; checkedAt: Date }
  | { state: 'down'; reason: string; checkedAt: Date };

/** How often the API is asked again, and how long it may take to answer. */
export const REFRESH_MS = 30_000;
export const TIMEOUT_MS = 5_000;

/**
 * Asks the API now, then every REFRESH_MS. An answer slower than TIMEOUT_MS counts as
 * a failure. A check still running is cancelled when a new one starts or on unmount.
 */
export function useApiHealth(): ApiHealth {
  const [health, setHealth] = useState<ApiHealth>({ state: 'checking' });

  useEffect(() => {
    let current: AbortController | null = null;

    const check = async () => {
      current?.abort();
      const controller = new AbortController();
      current = controller;
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, TIMEOUT_MS);
      try {
        const { version } = await fetchHealth(controller.signal);
        setHealth({ state: 'up', version, checkedAt: new Date() });
      } catch (error) {
        // A check replaced by a newer one, or cancelled on unmount, reports nothing.
        if (current !== controller) return;
        const reason = timedOut
          ? `No answer within ${TIMEOUT_MS / 1000} s`
          : error instanceof Error
            ? error.message
            : 'Unknown error';
        setHealth({ state: 'down', reason, checkedAt: new Date() });
      } finally {
        clearTimeout(timer);
      }
    };

    void check();
    const interval = setInterval(() => void check(), REFRESH_MS);
    return () => {
      clearInterval(interval);
      const pending = current;
      current = null;
      pending?.abort();
    };
  }, []);

  return health;
}
