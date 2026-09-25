import { REFRESH_MS, TIMEOUT_MS } from './api/config';
import type { Failure } from './api/health';

/** Words of the interface, in one place: English first, French later (ADR 010). */
export const TEXT = {
  tagline: 'Energy digital twin',
  statusTitle: 'API status',
  statusIntro: `The dashboard asks the API every ${REFRESH_MS / 1000} seconds.`,
  states: { checking: 'Checking…', up: 'Online', down: 'Unavailable' },
  labels: { version: 'Version', lastCheck: 'Last check', endpoint: 'Endpoint', reason: 'Reason' },
} as const;

/** Why a check failed, in plain words rather than the browser's own messages. */
export function reasonText(failure: Failure): string {
  switch (failure.kind) {
    case 'timeout':
      return `No answer within ${TIMEOUT_MS / 1000} s`;
    case 'network':
      return 'No network connection';
    case 'http':
      // 502, 503 and 504 come from the relay in front of the API when the API does not answer.
      return [502, 503, 504].includes(failure.status)
        ? `The API is not responding (HTTP ${failure.status})`
        : `The API returned an error (HTTP ${failure.status})`;
    case 'format':
      return 'Unexpected answer from the API';
    case 'unexpected':
      return 'Unexpected error, see the browser console';
  }
}
