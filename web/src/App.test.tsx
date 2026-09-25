import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { fetchHealth } from './api/health';

vi.mock('./api/health', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api/health')>()),
  fetchHealth: vi.fn(),
}));

describe('App', () => {
  afterEach(() => {
    vi.mocked(fetchHealth).mockReset();
  });

  it('shows the status screen, then the state the API reports', async () => {
    vi.mocked(fetchHealth).mockResolvedValue({ status: 'ok', version: '0.1.0' });
    render(<App />);

    expect(screen.getByText('Ampère')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'API status' })).toBeInTheDocument();
    expect(await screen.findByText('Online')).toBeInTheDocument();
    expect(screen.getByText('0.1.0')).toBeInTheDocument();
  });
});
