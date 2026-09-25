import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TEXT } from '../text';
import { ApiStatusCard } from './ApiStatusCard';

// 12:32:05 UTC is 14:32:05 in Paris on that day (summer time).
const checkedAt = new Date('2026-09-25T12:32:05Z');

/** The labels of the details, in order. */
const terms = () => screen.getAllByRole('term').map((term) => term.textContent);

describe('ApiStatusCard', () => {
  it('says it is checking, and shows only the endpoint', () => {
    render(<ApiStatusCard health={{ state: 'checking' }} />);

    expect(screen.getByRole('status')).toHaveTextContent(/^Checking…$/);
    expect(terms()).toEqual([TEXT.labels.endpoint]);
    expect(screen.getByText('/api/healthz')).toBeInTheDocument();
  });

  it('shows the version and the time of the check, in Paris time, when the API is up', () => {
    render(<ApiStatusCard health={{ state: 'up', version: '0.1.0', checkedAt }} />);

    expect(screen.getByRole('status')).toHaveTextContent(/^Online$/);
    expect(terms()).toEqual([TEXT.labels.version, TEXT.labels.lastCheck, TEXT.labels.endpoint]);
    expect(screen.getByText('0.1.0')).toBeInTheDocument();
    expect(screen.getByText('14:32:05 CEST')).toBeInTheDocument();
  });

  it('shows winter time, and midnight as 00', () => {
    render(
      <ApiStatusCard
        health={{ state: 'up', version: '0.1.0', checkedAt: new Date('2026-01-14T23:05:00Z') }}
      />,
    );

    expect(screen.getByText('00:05:00 CET')).toBeInTheDocument();
  });

  it('gives the reason in plain words when the API is unavailable', () => {
    render(
      <ApiStatusCard
        health={{ state: 'down', failure: { kind: 'http', status: 502 }, checkedAt }}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent(/^Unavailable$/);
    expect(terms()).toEqual([TEXT.labels.reason, TEXT.labels.lastCheck, TEXT.labels.endpoint]);
    expect(screen.getByText('The API is not responding (HTTP 502)')).toBeInTheDocument();
  });
});
