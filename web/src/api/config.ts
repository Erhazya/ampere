/** Where the dashboard asks for the health of the API (relayed to GET /healthz, ADR 016). */
export const HEALTH_URL = '/api/healthz';

/** Time between the end of a check and the start of the next one. */
export const REFRESH_MS = 30_000;

/** How long one check may wait for an answer. */
export const TIMEOUT_MS = 5_000;
