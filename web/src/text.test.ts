// @vitest-environment node
import { describe, expect, it } from 'vitest';
import type { Failure } from './api/health';
import type { RecentFailure } from './api/recent';
import { reasonText, recentReason, TEXT } from './text';

describe('reasonText', () => {
  it.each<[Failure, string]>([
    [{ kind: 'timeout' }, 'No answer within 5 s'],
    [{ kind: 'network' }, 'Cannot reach the server'],
    [{ kind: 'http', status: 502 }, 'The API is not responding (HTTP 502)'],
    [{ kind: 'http', status: 503 }, 'The API is not responding (HTTP 503)'],
    [{ kind: 'http', status: 504 }, 'The API is not responding (HTTP 504)'],
    [{ kind: 'http', status: 500 }, 'The API returned an error (HTTP 500)'],
    [{ kind: 'format' }, 'Unexpected answer from the API'],
    [{ kind: 'unexpected' }, 'Unexpected error, see the browser console'],
  ])('%j reads "%s"', (failure, text) => {
    expect(reasonText(failure)).toBe(text);
  });
});

describe('recentReason', () => {
  it.each<[RecentFailure, string]>([
    [{ kind: 'none' }, TEXT.data.none.detail],
    [{ kind: 'timeout' }, 'No answer within 10 s.'],
    [{ kind: 'network' }, 'Cannot reach the server.'],
    [
      { kind: 'http', status: 503 },
      'The API could not use its last export (HTTP 503). The next daily job, after 14:00, writes a new one.',
    ],
    [{ kind: 'http', status: 502 }, 'The API is not responding (HTTP 502).'],
    [{ kind: 'http', status: 504 }, 'The API is not responding (HTTP 504).'],
    [{ kind: 'http', status: 500 }, 'The API returned an error (HTTP 500).'],
    [
      { kind: 'format' },
      "The answer of the API did not pass the dashboard's checks. The browser console says where.",
    ],
    [{ kind: 'unexpected' }, 'Unexpected error, see the browser console.'],
  ])('%j reads "%s"', (failure, text) => {
    expect(recentReason(failure)).toBe(text);
  });
});
