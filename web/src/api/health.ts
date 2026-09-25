/** Answer of GET /healthz when the API is up (see src/ampere/api.py). */
export interface Health {
  status: 'ok';
  version: string;
}

/**
 * Asks the API whether it is up. Rejects when the request fails, when the API
 * answers with an error status, or when the answer does not have the expected shape.
 */
export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch('/api/healthz', { headers: { Accept: 'application/json' }, signal });
  if (!response.ok) {
    throw new Error(`The API answered with status ${response.status}`);
  }
  const body: unknown = await response.json();
  if (!isHealth(body)) {
    throw new Error('The API answer does not have the expected shape');
  }
  return body;
}

/** Checks at run time what TypeScript cannot know about an answer from the network. */
function isHealth(value: unknown): value is Health {
  if (typeof value !== 'object' || value === null) return false;
  const { status, version } = value as Record<string, unknown>;
  return status === 'ok' && typeof version === 'string';
}
