import { HEALTH_URL } from './config';

/** Answer of GET /healthz when the API is up (see src/ampere/api.py). */
export interface Health {
  status: 'ok';
  version: string;
}

/** A failure found in the answer itself: an error status, or content that is not the health data. */
type AnswerFailure = { kind: 'http'; status: number } | { kind: 'format' };

/** Why a check failed, in terms the interface puts into words (text.ts). */
export type Failure =
  AnswerFailure | { kind: 'timeout' } | { kind: 'network' } | { kind: 'unexpected' };

/** The error thrown for an AnswerFailure. */
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
 * status (from the API, or from the relay when the API is down) or is not the health data;
 * otherwise with the error of fetch itself: a network failure, before or while the body is
 * read, or `signal` aborting.
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
  let body: unknown;
  try {
    body = await response.json();
  } catch (error) {
    // Only a body that is not JSON (an HTML page, say) is an unexpected answer.
    if (error instanceof SyntaxError) throw new HealthError({ kind: 'format' }, { cause: error });
    throw error;
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
