import { describe, expect, it } from 'vitest';
import { screenOf } from './useScreen';

describe('screenOf', () => {
  it('names the state of the API by #/status, and the Data screen otherwise', () => {
    expect(screenOf('#/status')).toBe('status');
    expect(screenOf('')).toBe('data');
    expect(screenOf('#/')).toBe('data');
    expect(screenOf('#/elsewhere')).toBe('data');
  });
});
