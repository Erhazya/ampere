import { describe, expect, it } from 'vitest';
import type { Failure } from './api/health';
import { reasonText } from './text';

describe('reasonText', () => {
  it.each<[Failure, string]>([
    [{ kind: 'timeout' }, 'No answer within 5 s'],
    [{ kind: 'network' }, 'No network connection'],
    [{ kind: 'http', status: 502 }, 'The API is not responding (HTTP 502)'],
    [{ kind: 'http', status: 504 }, 'The API is not responding (HTTP 504)'],
    [{ kind: 'http', status: 500 }, 'The API returned an error (HTTP 500)'],
    [{ kind: 'format' }, 'Unexpected answer from the API'],
    [{ kind: 'unexpected' }, 'Unexpected error, see the browser console'],
  ])('%j reads "%s"', (failure, text) => {
    expect(reasonText(failure)).toBe(text);
  });
});
