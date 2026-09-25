import { HEALTH_URL } from './config';

/** Answer of GET /healthz when the API is up (see src/ampere/api.py). */
export interface Health {
  status: 'ok';
  version: string;
}

/** Why a check failed, in terms the interface puts into words (text.ts). */
export type Failure =
  | { kind: 'timeout' }
  | { kind: 'network' }
  | { kind: 'http'; status: number }
  | { kind: 'format' }
  | { kind: 'unexpected' };

type AnswerFailure = Extract<Failure, { kind: 'http' } | { kind: 'format' }>;

/** A failure found in the answer itself: an error status, or content that is not the health data. */
export class HealthError extends Error {
  readonly failure: AnswerFailure;

  constructor(failure: AnswerFailure, options?: ErrorOptions) {
    super(failure.kind === 'http' ? `HTTP status ${failure.status}` : 'Unexpected answer', options);
    this.name = 'HealthError';
    this.failure = failure;
  }
}

/**
 * Asks the API whether it is up. Rejects with a HealthError when the answer has an error
 * status (from the API, or from the relay when the API is down) or is not the health
 * data; with the error of fetch itself when the network fails or `signal` aborts.
 */
export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch(HEALTH_URL, {
    headers: { Accept: 'application/json' },
    // Always a fresh answer, whatever a proxy may add later.
    cache: 'no-store',
    signal,
  });
  if (!response.ok) {
    throw new HealthError({ kind: 'http', status: response.status });
  }
  // A relay that loses the /api rule answers 200 with the dashboard's own HTML page.
  if (!(response.headers.get('Content-Type') ?? '').includes('application/json')) {
    throw new HealthError({ kind: 'format' });
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new HealthError({ kind: 'format' }, { cause: error });
  }
  if (!isHealth(body)) {
    throw new HealthError({ kind: 'format' });
  }
  return body;
}

/** Checks at run time what TypeScript cannot know about an answer from the network. */
function isHealth(value: unknown): value is Health {
  return (
    typeof value === 'object' &&
    value !== null &&
    'status' in value &&
    value.status === 'ok' &&
    'version' in value &&
    typeof value.version === 'string'
  );
}
