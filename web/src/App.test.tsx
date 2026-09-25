import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

// Only the network is simulated here: the request, the hook and the card run for real.
describe('App', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the status screen, then the version the API reports', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(Response.json({ status: 'ok', version: '0.1.0' })),
    );
    render(<App />);

    expect(screen.getByText('Ampère')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'API status' })).toBeInTheDocument();
    expect(await screen.findByText('0.1.0')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/^Online$/);
  });

  it('says the API is not responding when the relay answers 502', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          new Response('', { status: 502, headers: { 'Content-Type': 'text/plain' } }),
        ),
    );
    render(<App />);

    expect(await screen.findByText('The API is not responding (HTTP 502)')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/^Unavailable$/);
  });
});
