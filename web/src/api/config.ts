/** Where the dashboard asks for the health of the API: GET /api/healthz (ADR 018). */
export const HEALTH_URL = '/api/healthz';

/** Time between the end of a check and the start of the next one. */
export const REFRESH_MS = 30_000;

/** How long one check may wait for an answer. */
export const TIMEOUT_MS = 5_000;

/** Where the dashboard asks for the recent days of the Data screen (ADR 029). */
export const RECENT_URL = '/api/data/recent';

/** How long the request for the recent days may wait: the export weighs about 100 kB. */
export const RECENT_TIMEOUT_MS = 10_000;
