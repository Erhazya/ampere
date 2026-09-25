/** Where the dashboard asks for the health of the API (relayed to GET /healthz, ADR 016). */
export const HEALTH_URL = '/api/healthz';

/** Answer of GET /healthz when the API is up (see src/ampere/api.py). */
export interface Health {
  status: 'ok';
  version: string;
}

/**
 * Asks the API whether it is up. Rejects when the request fails, when the answer has
 * an error status (from the API, or from the relay when the API is down), or when the
 * answer does not have the expected shape.
 */
export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch(HEALTH_URL, { headers: { Accept: 'application/json' }, signal });
  if (!response.ok) {
    throw new Error(`HTTP status ${response.status}`);
  }
  const body: unknown = await response.json();
  if (!isHealth(body)) {
    throw new Error('Unexpected answer from the API');
  }
  return body;
}

/** Checks at run time what TypeScript cannot know about an answer from the network. */
function isHealth(value: unknown): value is Health {
  if (typeof value !== 'object' || value === null) return false;
  const { status, version } = value as Record<string, unknown>;
  return status === 'ok' && typeof version === 'string';
}
