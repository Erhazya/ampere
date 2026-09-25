import { REFRESH_MS } from './api/useApiHealth';

/** Words of the interface, in one place: English first, French later (ADR 010). */
export const TEXT = {
  tagline: 'Energy digital twin',
  statusTitle: 'API status',
  statusIntro: `The dashboard asks the API every ${REFRESH_MS / 1000} seconds.`,
  states: { checking: 'Checking…', up: 'Online', down: 'Unreachable' },
  labels: { version: 'Version', lastCheck: 'Last check', endpoint: 'Endpoint', reason: 'Reason' },
} as const;
