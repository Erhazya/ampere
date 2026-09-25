import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ApiStatusCard } from './ApiStatusCard';

// 12:32:05 UTC is 14:32:05 in Paris on that day (summer time).
const checkedAt = new Date('2026-09-25T12:32:05Z');

describe('ApiStatusCard', () => {
  it('says it is checking, and shows only the endpoint', () => {
    render(<ApiStatusCard health={{ state: 'checking' }} />);

    expect(screen.getByRole('status')).toHaveTextContent('Checking…');
    expect(screen.getByText('/api/healthz')).toBeInTheDocument();
    expect(screen.queryByText('Last check')).not.toBeInTheDocument();
  });

  it('shows the version and the time of the check, in Paris time, when the API is up', () => {
    render(<ApiStatusCard health={{ state: 'up', version: '0.1.0', checkedAt }} />);

    expect(screen.getByRole('status')).toHaveTextContent('Online');
    expect(screen.getByText('0.1.0')).toBeInTheDocument();
    expect(screen.getByText('14:32:05')).toBeInTheDocument();
  });

  it('gives the reason when the API is down', () => {
    render(<ApiStatusCard health={{ state: 'down', reason: 'No answer within 5 s', checkedAt }} />);

    expect(screen.getByRole('status')).toHaveTextContent('Unreachable');
    expect(screen.getByText('No answer within 5 s')).toBeInTheDocument();
    expect(screen.queryByText('Version')).not.toBeInTheDocument();
  });
});
